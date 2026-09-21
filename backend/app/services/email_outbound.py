"""Outbound mail: queueing (API side) and delivery (worker side).

The API never opens an SMTP connection. It writes an ``outbound_emails`` row
in the same transaction as the ticket message, and the worker drains the
queue. That keeps a slow mail server from blocking an agent's reply, survives
a restart mid-send, and means the API container needs no network egress.
"""

from __future__ import annotations

import contextlib
import logging
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, OutboundEmail, Ticket, TicketMessage
from app.security import mail_tokens
from app.services import storage
from app.services.email_parse import REPLY_DELIMITER
from app.services.mail_tls import build_context

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
# 1m, 4m, 9m, 16m, 25m - enough to ride out a provider blip without hammering.
BACKOFF_BASE_SECONDS = 60


def new_message_id() -> str:
    domain = settings.smtp_from_email.split("@")[-1] if "@" in settings.smtp_from_email else None
    return make_msgid(idstring=uuid.uuid4().hex[:10], domain=domain)


def _thread_headers(db: Session, ticket: Ticket) -> tuple[str | None, str | None]:
    """Build In-Reply-To / References so mail clients thread our reply.

    We reply to the most recent message on the ticket that has a Message-ID,
    and carry forward the whole chain.
    """
    rows = (
        db.execute(
            select(TicketMessage.rfc822_message_id, TicketMessage.references)
            .where(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.rfc822_message_id.isnot(None),
                TicketMessage.kind.in_(("inbound", "outbound")),
            )
            .order_by(TicketMessage.created_at.desc())
            .limit(1)
        )
        .first()
    )
    if not rows:
        return None, None

    parent_id, parent_refs = rows
    references = " ".join(filter(None, [(parent_refs or "").strip(), parent_id]))
    return parent_id, references.strip() or None


def queue_message(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    *,
    to_email: str,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
) -> OutboundEmail | None:
    """Queue one email for a ticket message.

    Returns ``None`` when outbound mail is not configured, so a helpdesk
    without SMTP still works as a GUI-only tool.
    """
    if not settings.outbound_email_configured:
        log.warning("outbound mail not configured; skipping send for ticket %s", ticket.key)
        return None
    if not to_email:
        return None

    rfc822_id = new_message_id()
    in_reply_to, references = _thread_headers(db, ticket)

    # A plain address: the ticket reference travels in the subject and the
    # body, never in the local part.
    reply_to = settings.smtp_reply_to_email or settings.smtp_from_email

    outbound = OutboundEmail(
        ticket_id=ticket.id,
        message_id=message.id,
        to_email=to_email,
        subject=mail_tokens.tag_subject(
            subject or ticket.subject, ticket.number, ticket.reply_token
        ),
        body_text=body_text if body_text is not None else message.body_text,
        body_html=body_html if body_html is not None else message.body_html,
        rfc822_message_id=rfc822_id,
        in_reply_to=in_reply_to,
        references=references,
        reply_to=reply_to,
    )
    db.add(outbound)

    # Store our own Message-ID on the ticket message so that when the customer
    # replies, their In-Reply-To points straight back at this ticket.
    message.rfc822_message_id = rfc822_id
    message.in_reply_to = in_reply_to
    message.references = references
    db.flush()
    return outbound


def compose_body(ticket: Ticket, body_text: str, *, signature: str = "") -> str:
    """Wrap an agent reply with the reply delimiter and a ticket footer.

    The footer carries the signed body reference. It looks like noise, but it
    is what lets a reply be matched when the sender has rewritten the subject,
    and it survives in the quoted history of essentially every mail client.
    """
    parts = [REPLY_DELIMITER, "", body_text.strip()]
    if signature:
        parts += ["", signature.strip()]
    parts += [
        "",
        "--",
        f"{settings.smtp_from_name} - ticket {ticket.key}",
        "Reply to this email and your message will be added to the ticket.",
        mail_tokens.body_reference(ticket.number, ticket.reply_token),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# delivery (worker only)
# ---------------------------------------------------------------------------
def _build_mime(db: Session, row: OutboundEmail) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = row.to_email
    message["Subject"] = row.subject
    message["Message-ID"] = row.rfc822_message_id
    message["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
    if row.reply_to:
        message["Reply-To"] = row.reply_to
    if row.in_reply_to:
        message["In-Reply-To"] = row.in_reply_to
    if row.references:
        message["References"] = row.references
    # Tell well-behaved auto-responders not to reply to us, which is half of
    # mail-loop prevention (the other half is detect_auto_reply on the way in).
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "OOF, AutoReply"

    message.set_content(row.body_text or "")
    if row.body_html:
        message.add_alternative(row.body_html, subtype="html")

    if row.message_id:
        attachments = (
            db.query(Attachment)
            .filter(Attachment.message_id == row.message_id, Attachment.is_inline.is_(False))
            .all()
        )
        for attachment in attachments:
            try:
                data = storage.read(attachment.storage_path)
            except (OSError, ValueError) as exc:
                log.warning("skipping unreadable attachment %s: %s", attachment.id, exc)
                continue
            declared = attachment.content_type or "application/octet-stream"
            maintype, _, subtype = declared.partition("/")
            message.add_attachment(
                data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.filename,
            )
    return message


def _connect() -> smtplib.SMTP:
    context = build_context(
        host=settings.smtp_host,
        ca_cert=settings.smtp_ca_cert,
        insecure=settings.smtp_tls_insecure,
    )
    if settings.smtp_security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=30, context=context
        )
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        client.ehlo()
        if settings.smtp_security == "starttls":
            client.starttls(context=context)
            client.ehlo()
    if settings.smtp_username:
        client.login(settings.smtp_username, settings.smtp_password)
    return client


def flush_queue(db: Session, limit: int = 25) -> tuple[int, int]:
    """Send queued mail. Returns ``(sent, failed)``."""
    if not settings.outbound_email_configured:
        return 0, 0

    now = datetime.now(UTC)
    pending = (
        db.query(OutboundEmail)
        .filter(
            OutboundEmail.status.in_(("queued", "sending")),
            OutboundEmail.next_attempt_at <= now,
            OutboundEmail.attempts < MAX_ATTEMPTS,
        )
        .order_by(OutboundEmail.created_at)
        .limit(limit)
        .all()
    )
    if not pending:
        return 0, 0

    sent = failed = 0
    client: smtplib.SMTP | None = None
    try:
        client = _connect()
        for row in pending:
            row.attempts += 1
            row.status = "sending"
            db.flush()
            try:
                client.send_message(_build_mime(db, row))
            except (smtplib.SMTPException, OSError, ValueError) as exc:
                failed += 1
                row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    log.error("giving up on outbound email %s: %s", row.id, row.last_error)
                else:
                    row.status = "queued"
                    row.next_attempt_at = now + timedelta(
                        seconds=BACKOFF_BASE_SECONDS * row.attempts**2
                    )
                    log.warning("outbound email %s failed, will retry: %s", row.id, exc)
            else:
                sent += 1
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
                row.last_error = ""
            db.flush()
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # Connection-level failure: leave everything queued for the next pass.
        log.error("SMTP connection to %s failed: %s", settings.smtp_host, exc)
        for row in pending:
            if row.status == "sending":
                row.status = "queued"
                row.last_error = f"connection: {exc}"[:2000]
                row.next_attempt_at = now + timedelta(
                    seconds=BACKOFF_BASE_SECONDS * max(1, row.attempts) ** 2
                )
        db.flush()
    finally:
        if client is not None:
            with contextlib.suppress(smtplib.SMTPException, OSError):
                client.quit()

    db.commit()
    return sent, failed
