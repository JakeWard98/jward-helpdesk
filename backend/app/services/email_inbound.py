"""Turning an inbound email into a ticket or a reply on an existing ticket.

Routing precedence, strongest signal first:

  1. **Signed subject tag** - ``[TKT-1042-9f3c1ab27d0e]``. The HMAC is verified
     against the ticket's stored ``reply_token``, so it cannot be forged.
  2. **Signed body reference** - ``[ref:1042-9f3c1ab27d0e]`` anywhere in the
     raw body, including the quoted history. Catches replies where the sender
     rewrote the subject.
  3. **Threading headers** - ``In-Reply-To`` / ``References`` matched against
     Message-IDs we previously sent or ingested.
  4. **Unsigned subject tag** - a bare ``[TKT-1042]``, accepted *only* when the
     sender already has a place on that ticket. A subject line is trivially
     copied, so on its own it must never grant access to somebody else's
     ticket.
  5. Otherwise a new ticket, on the fallback template.

Every message is recorded in ``processed_inbound_emails`` by Message-ID, so
re-reading a mailbox can never duplicate a ticket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ProcessedInboundEmail, Ticket, TicketMessage, User
from app.security import mail_tokens
from app.services import audit, email_outbound, storage, templates, tickets
from app.services.email_parse import ParsedEmail, parse_email, strip_quoted_reply
from app.services.sanitize import prepare_email_html

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    outcome: str  # created | appended | duplicate | ignored | error
    ticket_id: str | None = None
    ticket_key: str | None = None
    message_id: str | None = None
    detail: str = ""


def _synthetic_message_id(raw: bytes) -> str:
    """Stand-in Message-ID for mail that arrives without one.

    Hashing the raw bytes keeps de-duplication working for such messages.
    """
    digest = sha256(raw).hexdigest()[:40]
    return f"<synthetic-{digest}@jward-helpdesk.invalid>"


def _already_processed(db: Session, message_id: str) -> ProcessedInboundEmail | None:
    return db.get(ProcessedInboundEmail, message_id)


def _verified(db: Session, number: int, signature: str, source: str) -> Ticket | None:
    """Load a ticket only if the supplied signature matches it."""
    ticket = db.query(Ticket).filter(Ticket.number == number).one_or_none()
    if ticket is None:
        log.warning("%s referenced unknown ticket %s", source, number)
        return None
    if not mail_tokens.verify(number, ticket.reply_token, signature):
        log.warning("%s for ticket %s failed the signature check", source, number)
        return None
    return ticket


def _find_by_signed_subject(db: Session, parsed: ParsedEmail) -> Ticket | None:
    hit = mail_tokens.parse_subject(parsed.subject)
    if not hit or hit[1] is None:
        return None
    return _verified(db, hit[0], hit[1], "subject tag")


def _find_by_signed_body(db: Session, parsed: ParsedEmail) -> Ticket | None:
    # Search the raw bodies, not the quote-stripped text: the reference is
    # usually sitting in exactly the quoted history that gets trimmed.
    hit = mail_tokens.parse_body(parsed.text_body, parsed.html_body)
    if not hit:
        return None
    return _verified(db, hit[0], hit[1], "body reference")


def _find_by_threading(db: Session, parsed: ParsedEmail) -> Ticket | None:
    candidates = [parsed.in_reply_to, *reversed(parsed.references)]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    row = db.execute(
        select(TicketMessage.ticket_id)
        .where(TicketMessage.rfc822_message_id.in_(candidates))
        .order_by(TicketMessage.created_at.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    return db.get(Ticket, row[0])


def _sender_belongs_to_ticket(db: Session, ticket: Ticket, sender_email: str) -> bool:
    """Has this address already taken part in the ticket?"""
    sender_email = (sender_email or "").lower()
    if not sender_email:
        return False
    if ticket.requester and ticket.requester.email.lower() == sender_email:
        return True
    user = db.query(User).filter(User.email == sender_email).one_or_none()
    if user and user.is_staff:
        return True
    existing = db.execute(
        select(TicketMessage.id)
        .where(
            TicketMessage.ticket_id == ticket.id,
            TicketMessage.author_email == sender_email,
        )
        .limit(1)
    ).first()
    return existing is not None


def _find_by_unsigned_subject(db: Session, parsed: ParsedEmail) -> Ticket | None:
    """Last resort: a bare ``[TKT-1042]`` with no signature.

    Only honoured for someone already on the ticket. Ticket numbers are
    sequential, so without that check anyone could guess one and post into a
    stranger's ticket.
    """
    hit = mail_tokens.parse_subject(parsed.subject)
    if not hit or hit[1] is not None:
        return None  # signed tags were already handled, and failed
    number = hit[0]
    ticket = db.query(Ticket).filter(Ticket.number == number).one_or_none()
    if ticket is None:
        return None
    if not _sender_belongs_to_ticket(db, ticket, parsed.from_email):
        log.warning(
            "ignoring subject tag for ticket %s: sender %s is not a participant",
            ticket.key,
            parsed.from_email,
        )
        return None
    return ticket


def find_ticket(db: Session, parsed: ParsedEmail) -> tuple[Ticket | None, str]:
    """Locate the ticket this mail belongs to, with the reason it matched."""
    for finder, reason in (
        (_find_by_signed_subject, "signed-subject"),
        (_find_by_signed_body, "signed-body-ref"),
        (_find_by_threading, "threading-headers"),
        (_find_by_unsigned_subject, "unsigned-subject"),
    ):
        ticket = finder(db, parsed)
        if ticket:
            return ticket, reason
    return None, "none"


def _is_own_address(email: str) -> bool:
    """Guard against ingesting our own mail and looping."""
    email = (email or "").lower()
    ours = {
        settings.smtp_from_email.lower(),
        settings.smtp_reply_to_email.lower(),
        settings.imap_username.lower(),
    }
    return bool(email) and email in {o for o in ours if o}


def _store_attachments(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    parsed: ParsedEmail,
) -> dict[str, str]:
    """Save every attachment and return a cid -> URL map for inline images."""
    cid_to_url: dict[str, str] = {}
    for item in parsed.attachments:
        try:
            attachment = tickets.attach_file(
                db,
                ticket,
                message,
                filename=item.filename,
                content_type=item.content_type,
                data=item.data,
                content_id=item.content_id,
                is_inline=item.is_inline,
            )
        except storage.AttachmentTooLarge as exc:
            log.warning("attachment on ticket %s rejected: %s", ticket.key, exc)
            continue
        except OSError as exc:
            log.error("could not store attachment on ticket %s: %s", ticket.key, exc)
            continue

        if item.content_id:
            cid_to_url[item.content_id] = f"/api/attachments/{attachment.id}/inline"
    return cid_to_url


def ingest(db: Session, raw: bytes) -> IngestResult:
    """Process one raw message. Caller owns the transaction."""
    if len(raw) > settings.max_email_bytes:
        log.warning("dropping oversized email (%s bytes)", len(raw))
        return IngestResult(outcome="ignored", detail="message exceeds MAX_EMAIL_MB")

    parsed = parse_email(raw)
    message_key = parsed.message_id or _synthetic_message_id(raw)

    existing = _already_processed(db, message_key)
    if existing:
        return IngestResult(
            outcome="duplicate", ticket_id=existing.ticket_id, detail="already ingested"
        )

    if not parsed.from_email:
        db.add(ProcessedInboundEmail(rfc822_message_id=message_key, outcome="ignored"))
        return IngestResult(outcome="ignored", detail="no usable From address")

    if _is_own_address(parsed.from_email):
        db.add(ProcessedInboundEmail(rfc822_message_id=message_key, outcome="ignored"))
        log.info("ignoring mail from our own address (%s)", parsed.from_email)
        return IngestResult(outcome="ignored", detail="mail from the helpdesk's own address")

    ticket, match_reason = find_ticket(db, parsed)

    # An auto-reply that matches nothing would otherwise create a ticket, whose
    # acknowledgement would trigger another auto-reply. Stop the loop here.
    if ticket is None and parsed.is_auto_reply:
        db.add(ProcessedInboundEmail(rfc822_message_id=message_key, outcome="ignored"))
        log.info("ignoring unmatched auto-reply from %s", parsed.from_email)
        return IngestResult(outcome="ignored", detail="unmatched auto-reply")

    requester = tickets.get_or_create_requester(db, parsed.from_email, parsed.from_name)
    body_text = strip_quoted_reply(parsed.text_body)

    if ticket is None:
        fallback = templates.get_fallback(db)
        ticket, message = tickets.create_ticket(
            db,
            subject=parsed.subject,
            requester=requester,
            body_text=body_text,
            body_html=None,  # set below, once attachment URLs exist
            template=fallback,
            field_values={},
            source="email",
            author=requester,
            author_email=parsed.from_email,
            author_name=parsed.from_name,
            message_kind="inbound",
            rfc822_message_id=parsed.message_id,
            in_reply_to=parsed.in_reply_to,
            references=" ".join(parsed.references) or None,
        )
        outcome = "created"
    else:
        message = tickets.add_message(
            db,
            ticket,
            kind="inbound",
            body_text=body_text,
            body_html=None,
            author=requester if requester.id != ticket.requester_id else ticket.requester,
            author_email=parsed.from_email,
            author_name=parsed.from_name,
            rfc822_message_id=parsed.message_id,
            in_reply_to=parsed.in_reply_to,
            references=" ".join(parsed.references) or None,
        )
        outcome = "appended"

    cid_to_url = _store_attachments(db, ticket, message, parsed)

    if parsed.html_body:
        safe_html, blocked = prepare_email_html(parsed.html_body, cid_to_url)
        message.body_html = safe_html
        message.remote_content_blocked = blocked
    db.flush()

    db.add(
        ProcessedInboundEmail(
            rfc822_message_id=message_key,
            ticket_id=ticket.id,
            outcome=outcome,
        )
    )
    audit.record(
        db,
        action=f"email.{outcome}",
        actor=requester,
        object_type="ticket",
        object_id=ticket.id,
        detail={
            "match": match_reason,
            "from": parsed.from_email,
            "attachments": len(parsed.attachments),
            "auto_reply": parsed.is_auto_reply,
        },
    )

    if outcome == "created" and not parsed.is_auto_reply:
        _send_acknowledgement(db, ticket, requester)

    log.info(
        "email from %s -> ticket %s (%s, match=%s)",
        parsed.from_email,
        ticket.key,
        outcome,
        match_reason,
    )
    return IngestResult(
        outcome=outcome,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        message_id=message.id,
        detail=match_reason,
    )


def _send_acknowledgement(db: Session, ticket: Ticket, requester: User) -> None:
    """Confirm receipt so the sender has the ticket number to reply against."""
    body = email_outbound.compose_body(
        ticket,
        (
            f"Thanks - your request has been logged as {ticket.key}.\n\n"
            "We will be in touch. Replying to this email adds your message to "
            "the same ticket."
        ),
    )
    message = tickets.add_message(
        db,
        ticket,
        kind="system",
        body_text=body,
        author=None,
        author_name=settings.smtp_from_name,
        author_email=settings.smtp_from_email,
    )
    email_outbound.queue_message(
        db,
        ticket,
        message,
        to_email=requester.email,
        subject=ticket.subject,
        body_text=body,
    )


def ingest_bytes(db: Session, raw: bytes) -> IngestResult:
    """Convenience wrapper that commits or rolls back around :func:`ingest`."""
    try:
        result = ingest(db, raw)
        db.commit()
        return result
    except Exception as exc:  # noqa: BLE001 - one bad mail must not stop the poller
        db.rollback()
        log.exception("failed to ingest inbound email")
        return IngestResult(outcome="error", detail=f"{type(exc).__name__}: {exc}")
