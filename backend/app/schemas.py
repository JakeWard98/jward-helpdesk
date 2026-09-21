"""Request and response models.

Response models exist so an endpoint can never accidentally leak a column it
was not meant to (password hashes, TOTP secrets, reply tokens).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MfaChallengeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class LoginResponse(BaseModel):
    status: Literal["authenticated", "mfa_required", "mfa_enrolment_required"]
    user: UserOut | None = None
    csrf_token: str | None = None


class MfaEnrolStartResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg: str


class MfaEnrolConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MfaEnrolConfirmResponse(BaseModel):
    recovery_codes: list[str]


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    role: str
    is_active: bool
    mfa_enabled: bool
    must_change_password: bool
    last_login_at: datetime | None = None


class UserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=200)
    role: Literal["admin", "agent", "requester"] = "requester"
    password: str | None = Field(default=None, max_length=256)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "agent", "requester"] | None = None
    is_active: bool | None = None
    reset_password: str | None = Field(default=None, max_length=256)
    clear_mfa: bool = False


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
class TemplateFieldOut(BaseModel):
    key: str
    label: str
    type: str
    required: bool = False
    options: list[str] = Field(default_factory=list)
    help: str = ""


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str
    icon: str
    fields: list[TemplateFieldOut]
    default_priority: str
    is_fallback: bool
    is_active: bool
    sort_order: int


class TemplateWriteRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    icon: str = Field(default="ticket", max_length=40)
    fields: list[dict[str, Any]] = Field(default_factory=list)
    default_priority: Literal["low", "normal", "high", "urgent"] = "normal"
    subject_prefix: str = Field(default="", max_length=80)
    is_fallback: bool = False
    is_active: bool = True
    sort_order: int = 100


# ---------------------------------------------------------------------------
# tickets
# ---------------------------------------------------------------------------
class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    is_inline: bool
    created_at: datetime


class UploadOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int


class TicketMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    author_name: str
    author_email: str
    body_text: str
    body_html: str | None
    remote_content_blocked: bool
    created_at: datetime
    attachments: list[AttachmentOut] = Field(default_factory=list)


class TicketSummaryOut(BaseModel):
    id: str
    key: str
    number: int
    subject: str
    status: str
    priority: str
    source: str
    requester_name: str
    requester_email: str
    assignee_name: str | None = None
    template_name: str | None = None
    created_at: datetime
    last_activity_at: datetime
    message_count: int = 0
    attachment_count: int = 0


class TicketDetailOut(TicketSummaryOut):
    template_id: str | None = None
    assignee_id: str | None = None
    field_values: dict[str, Any] = Field(default_factory=dict)
    template_fields: list[TemplateFieldOut] = Field(default_factory=list)
    messages: list[TicketMessageOut] = Field(default_factory=list)


class TicketCreateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50000)
    template_id: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    field_values: dict[str, Any] = Field(default_factory=dict)
    # Agents only: raise a ticket for somebody else.
    requester_email: EmailStr | None = None
    requester_name: str | None = Field(default=None, max_length=200)
    # Ids returned by POST /api/uploads.
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("subject")
    @classmethod
    def _strip_subject(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("subject cannot be blank")
        return v


class TicketReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=50000)
    # An internal note stays in the GUI and is never emailed.
    internal: bool = False
    # Agents can close the ticket in the same action as the reply.
    set_status: Literal["new", "open", "pending", "resolved", "closed"] | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)


class TicketUpdateRequest(BaseModel):
    status: Literal["new", "open", "pending", "resolved", "closed"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    assignee_id: str | None = None
    template_id: str | None = None
    field_values: dict[str, Any] | None = None
    subject: str | None = Field(default=None, max_length=500)


class TicketListResponse(BaseModel):
    items: list[TicketSummaryOut]
    total: int
    page: int
    page_size: int


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_label: str
    action: str
    object_type: str
    object_id: str
    ip_address: str
    detail: dict[str, Any]
    created_at: datetime


class MailHealthOut(BaseModel):
    outbound_configured: bool
    inbound_enabled: bool
    queued: int
    failed: int
    sent_last_24h: int
    last_inbound_at: datetime | None = None


LoginResponse.model_rebuild()
