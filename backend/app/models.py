"""Database models.

Naming notes:
  * ``TicketMessage.rfc822_message_id`` is the RFC 5322 ``Message-ID`` header.
    It is what lets a reply land on the right ticket, and it is unique so the
    same mail can never be ingested twice.
  * Secrets (passwords, session tokens, recovery codes) are only ever stored
    as hashes. TOTP secrets have to be reversible, so they are encrypted with
    a key derived from ``APP_SECRET``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# enumerations kept as plain strings so adding a value needs no migration
# ---------------------------------------------------------------------------
ROLES = ("admin", "agent", "requester")
TICKET_STATUSES = ("new", "open", "pending", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
MESSAGE_KINDS = ("inbound", "outbound", "note", "system")
OUTBOUND_STATUSES = ("queued", "sending", "sent", "failed")


# JSONB on Postgres (indexable, typed) and plain JSON elsewhere, so the test
# suite can run against SQLite without a database container.
JSONType = JSONB().with_variant(JSON(), "sqlite")


def _uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="requester")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # MFA
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rejects a TOTP code that was already spent, closing the replay window.
    last_totp_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)

    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recovery_codes: Mapped[list[RecoveryCode]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="ck_users_role"),
    )

    @property
    def is_staff(self) -> bool:
        return self.role in ("admin", "agent")


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="recovery_codes")


class AuthSession(Base):
    """Server-side session. The cookie only carries an opaque token."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # False between password check and TOTP check, so a half-finished login
    # cannot reach any ticket data.
    mfa_satisfied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class RateLimitBucket(Base):
    """Fixed-window counter shared by every API worker."""

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# ticketing
# ---------------------------------------------------------------------------
class Template(Base, TimestampMixin):
    """A ticket form. ``fields`` is a list of field definitions:

        [{"key": "asset_tag", "label": "Asset tag", "type": "text",
          "required": false, "options": [], "help": ""}]

    Exactly one template carries ``is_fallback``; that is the one mail-in
    tickets get, and an agent can re-template them later in the GUI.
    """

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[str] = mapped_column(String(40), nullable=False, default="ticket")
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    default_priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    subject_prefix: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    __table_args__ = (
        CheckConstraint(f"default_priority IN {TICKET_PRIORITIES}", name="ck_templates_priority"),
    )


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="web")

    requester_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assignee_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="SET NULL"), nullable=True
    )
    # Answers to the template's custom fields, keyed by field key.
    field_values: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    # Random per-ticket salt mixed into the reply-address HMAC, so guessing one
    # ticket's reply address tells you nothing about another's.
    reply_token: Mapped[str] = mapped_column(String(32), nullable=False)

    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    template: Mapped[Template | None] = relationship()
    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )

    __table_args__ = (
        CheckConstraint(f"status IN {TICKET_STATUSES}", name="ck_tickets_status"),
        CheckConstraint(f"priority IN {TICKET_PRIORITIES}", name="ck_tickets_priority"),
        Index("ix_tickets_status_activity", "status", "last_activity_at"),
    )

    @property
    def key(self) -> str:
        return f"TKT-{self.number}"


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="note")

    author_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    author_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Sanitised HTML only - see services/sanitize.py. Never render raw mail.
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the sanitiser stripped externally-hosted images (tracking pixels).
    remote_content_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # RFC 5322 threading headers.
    rfc822_message_id: Mapped[str | None] = mapped_column(
        String(500), unique=True, nullable=True, index=True
    )
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped[Ticket] = relationship(back_populates="messages")
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {MESSAGE_KINDS}", name="ck_ticket_messages_kind"),
    )

    @property
    def is_internal(self) -> bool:
        return self.kind == "note"


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Both nullable while an upload is still "pending": the browser uploads
    # first and the file is claimed when the ticket or reply is submitted, so
    # an outbound email is never sent before its attachments exist.
    ticket_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ticket_messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    uploaded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Path relative to ATTACHMENT_DIR; never user-controlled.
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)
    # Set for images referenced by cid: inside an HTML mail body.
    content_id: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    is_inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    message: Mapped[TicketMessage | None] = relationship(back_populates="attachments")


class OutboundEmail(Base):
    """Queue row. The API writes it; only the worker talks to the SMTP server."""

    __tablename__ = "outbound_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticket_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ticket_messages.id", ondelete="CASCADE"), nullable=True
    )
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    rfc822_message_id: Mapped[str] = mapped_column(String(500), nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {OUTBOUND_STATUSES}", name="ck_outbound_status"),
    )


class ProcessedInboundEmail(Base):
    """Idempotency ledger for IMAP ingestion."""

    __tablename__ = "processed_inbound_emails"

    rfc822_message_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    ticket_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Setting(Base):
    """Small key/value store for things an admin can change without a redeploy."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = [
    "Attachment",
    "AuditLog",
    "AuthSession",
    "OutboundEmail",
    "ProcessedInboundEmail",
    "RateLimitBucket",
    "RecoveryCode",
    "Setting",
    "Template",
    "Ticket",
    "TicketMessage",
    "User",
]
