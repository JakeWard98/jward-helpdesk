"""Ticket creation, messages and attachments."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.database import next_ticket_number
from app.models import Attachment, Template, Ticket, TicketMessage, User
from app.services import storage
from app.services.sanitize import clean_html, text_to_html

log = logging.getLogger(__name__)

OPEN_STATUSES = ("new", "open", "pending")


def get_or_create_requester(db: Session, email: str, display_name: str = "") -> User:
    """Find the user behind an email address, creating a requester if new.

    Created accounts have no password hash: they exist so the ticket has an
    owner and so the person can be invited later. They cannot log in until an
    admin sets a password, and :func:`verify_password` treats a null hash as a
    failed check rather than a bypass.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")

    user = db.query(User).filter(User.email == email).one_or_none()
    if user:
        if display_name and not user.display_name:
            user.display_name = display_name[:200]
        return user

    user = User(
        email=email,
        display_name=(display_name or email.split("@")[0])[:200],
        role="requester",
        is_active=True,
    )
    db.add(user)
    db.flush()
    log.info("created requester account for %s", email)
    return user


def create_ticket(
    db: Session,
    *,
    subject: str,
    requester: User,
    body_text: str = "",
    body_html: str | None = None,
    template: Template | None = None,
    field_values: dict[str, Any] | None = None,
    priority: str | None = None,
    source: str = "web",
    author: User | None = None,
    author_email: str = "",
    author_name: str = "",
    message_kind: str = "inbound",
    rfc822_message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    remote_content_blocked: bool = False,
) -> tuple[Ticket, TicketMessage]:
    """Create a ticket and its opening message."""
    subject = (subject or "").strip()[:500] or "(no subject)"
    if template and template.subject_prefix and not subject.startswith(template.subject_prefix):
        subject = f"{template.subject_prefix} {subject}".strip()[:500]

    ticket = Ticket(
        number=next_ticket_number(db),
        subject=subject,
        status="new",
        priority=priority or (template.default_priority if template else "normal"),
        source=source,
        requester_id=requester.id,
        template_id=template.id if template else None,
        field_values=field_values or {},
        reply_token=secrets.token_hex(16),
        last_activity_at=datetime.now(UTC),
    )
    db.add(ticket)
    db.flush()

    message = add_message(
        db,
        ticket,
        kind=message_kind,
        body_text=body_text,
        body_html=body_html,
        author=author or requester,
        author_email=author_email or requester.email,
        author_name=author_name or requester.display_name,
        rfc822_message_id=rfc822_message_id,
        in_reply_to=in_reply_to,
        references=references,
        remote_content_blocked=remote_content_blocked,
    )
    log.info("created ticket %s from %s", ticket.key, source)
    return ticket, message


def add_message(
    db: Session,
    ticket: Ticket,
    *,
    kind: str,
    body_text: str = "",
    body_html: str | None = None,
    author: User | None = None,
    author_email: str = "",
    author_name: str = "",
    rfc822_message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    remote_content_blocked: bool = False,
) -> TicketMessage:
    """Append a message to a ticket's timeline.

    ``body_html`` is sanitised again here even when the caller already did it,
    because this is the single choke point before anything reaches the GUI.
    """
    if body_html:
        body_html = clean_html(body_html)
    elif body_text and kind in ("inbound", "outbound"):
        body_html = text_to_html(body_text)

    message = TicketMessage(
        ticket_id=ticket.id,
        kind=kind,
        author_id=author.id if author else None,
        author_name=(author_name or (author.display_name if author else ""))[:200],
        author_email=(author_email or (author.email if author else ""))[:320],
        body_text=body_text or "",
        body_html=body_html,
        remote_content_blocked=remote_content_blocked,
        rfc822_message_id=rfc822_message_id,
        in_reply_to=in_reply_to,
        references=references,
    )
    db.add(message)

    ticket.last_activity_at = datetime.now(UTC)
    if kind == "outbound" and ticket.first_response_at is None:
        ticket.first_response_at = ticket.last_activity_at
    # A customer reply reopens a resolved ticket; agents close it again when done.
    if kind == "inbound" and ticket.status in ("resolved", "closed"):
        ticket.status = "open"
        ticket.resolved_at = None
        ticket.closed_at = None
    elif kind == "outbound" and ticket.status == "new":
        ticket.status = "pending"

    db.flush()
    return message


def attach_file(
    db: Session,
    ticket: Ticket | None,
    message: TicketMessage | None,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    content_id: str | None = None,
    is_inline: bool = False,
    uploaded_by: User | None = None,
) -> Attachment:
    """Persist an attachment. Raises ``storage.AttachmentTooLarge`` if oversized."""
    safe_name = storage.safe_filename(filename)
    sniffed = storage.sniff_content_type(data, content_type)
    suffix = ""
    if "." in safe_name:
        suffix = "." + safe_name.rsplit(".", 1)[-1].lower()[:10]

    relative, digest, size = storage.store_bytes(data, suffix=suffix)

    attachment = Attachment(
        ticket_id=ticket.id if ticket else None,
        message_id=message.id if message else None,
        uploaded_by_id=uploaded_by.id if uploaded_by else None,
        filename=safe_name,
        content_type=sniffed,
        size_bytes=size,
        sha256=digest,
        storage_path=relative,
        content_id=content_id,
        # Only real images are ever rendered inline.
        is_inline=is_inline and sniffed in storage.INLINE_SAFE_TYPES,
    )
    db.add(attachment)
    db.flush()
    return attachment


def claim_attachments(
    db: Session,
    ticket: Ticket,
    message: TicketMessage,
    attachment_ids: list[str],
    *,
    uploader: User,
) -> list[Attachment]:
    """Bind previously uploaded files to a ticket message.

    Only unclaimed uploads belonging to the same user are accepted, so an id
    guessed from somewhere else cannot pull another person's file onto a ticket.
    """
    if not attachment_ids:
        return []
    rows = (
        db.query(Attachment)
        .filter(
            Attachment.id.in_(attachment_ids[:20]),
            Attachment.ticket_id.is_(None),
            Attachment.message_id.is_(None),
            Attachment.uploaded_by_id == uploader.id,
        )
        .all()
    )
    for row in rows:
        row.ticket_id = ticket.id
        row.message_id = message.id
    db.flush()
    return rows


def purge_unclaimed_uploads(db: Session, older_than_hours: int = 24) -> int:
    """Delete uploads that were never attached to anything."""
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    stale = (
        db.query(Attachment)
        .filter(
            Attachment.ticket_id.is_(None),
            Attachment.message_id.is_(None),
            Attachment.created_at < cutoff,
        )
        .all()
    )
    for row in stale:
        # Storage is content-addressed, so only remove the file when no other
        # attachment row points at the same bytes.
        others = (
            db.query(Attachment)
            .filter(Attachment.storage_path == row.storage_path, Attachment.id != row.id)
            .count()
        )
        if others == 0:
            storage.delete(row.storage_path)
        db.delete(row)
    db.flush()
    return len(stale)


def set_status(db: Session, ticket: Ticket, status: str) -> None:
    now = datetime.now(UTC)
    ticket.status = status
    if status == "resolved":
        ticket.resolved_at = now
        ticket.closed_at = None
    elif status == "closed":
        ticket.closed_at = now
        ticket.resolved_at = ticket.resolved_at or now
    else:
        ticket.resolved_at = None
        ticket.closed_at = None
    ticket.last_activity_at = now
    db.flush()


def can_view(user: User, ticket: Ticket) -> bool:
    """Requesters see only their own tickets; agents and admins see everything."""
    if user.is_staff:
        return True
    return ticket.requester_id == user.id
