"""Admin: user management, audit trail, mail queue health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.config import settings
from app.deps import AdminUser, CurrentUser, DbSession
from app.models import AuditLog, OutboundEmail, ProcessedInboundEmail, RecoveryCode, User
from app.schemas import (
    AuditEntryOut,
    MailHealthOut,
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)
from app.security import passwords, sessions
from app.services import audit

router = APIRouter(tags=["admin"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: DbSession,
    context: CurrentUser,
    staff_only: bool = False,
) -> list[User]:
    """Agents get the staff list (for assignment); admins see everyone."""
    query = db.query(User)
    if staff_only or context.user.role != "admin":
        query = query.filter(User.role.in_(("admin", "agent")), User.is_active.is_(True))
    return query.order_by(User.display_name, User.email).limit(500).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    db: DbSession,
    context: AdminUser,
) -> User:
    email = payload.email.lower().strip()
    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing and existing.password_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email already has an account"
        )

    # A requester auto-created from an inbound email is upgraded in place, so
    # their existing tickets stay attached to the same account.
    user = existing or User(email=email)
    user.display_name = payload.display_name or user.display_name or email.split("@")[0]
    user.role = payload.role
    user.is_active = True

    if payload.password:
        problems = passwords.password_problems(payload.password, email=email)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password " + ", ".join(problems),
            )
        user.password_hash = passwords.hash_password(payload.password)
        # Admin-chosen passwords are temporary by definition.
        user.must_change_password = True

    db.add(user)
    db.flush()
    audit.record(
        db,
        action="user.created",
        actor=context.user,
        object_type="user",
        object_id=user.id,
        ip_address=sessions.client_ip(request),
        detail={"email": user.email, "role": user.role},
    )
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    request: Request,
    db: DbSession,
    context: AdminUser,
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    changes: dict[str, object] = {}

    if payload.display_name is not None:
        user.display_name = payload.display_name[:200]
        changes["display_name"] = user.display_name

    if payload.role is not None and payload.role != user.role:
        _guard_last_admin(db, user, new_role=payload.role, context_user_id=context.user.id)
        user.role = payload.role
        changes["role"] = payload.role

    if payload.is_active is not None and payload.is_active != user.is_active:
        if not payload.is_active:
            _guard_last_admin(db, user, new_role=None, context_user_id=context.user.id)
            sessions.revoke_all_for_user(db, user.id)
        user.is_active = payload.is_active
        changes["is_active"] = payload.is_active

    if payload.reset_password:
        problems = passwords.password_problems(payload.reset_password, email=user.email)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password " + ", ".join(problems),
            )
        user.password_hash = passwords.hash_password(payload.reset_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        sessions.revoke_all_for_user(db, user.id)
        changes["password_reset"] = True

    if payload.clear_mfa:
        # Recovery path for a lost authenticator. Logged loudly because it
        # removes a factor from an account.
        user.mfa_enabled = False
        user.totp_secret_encrypted = None
        user.last_totp_counter = None
        db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
        sessions.revoke_all_for_user(db, user.id)
        changes["mfa_cleared"] = True

    audit.record(
        db,
        action="user.updated",
        actor=context.user,
        object_type="user",
        object_id=user.id,
        ip_address=sessions.client_ip(request),
        detail=changes,
    )
    db.commit()
    return user


def _guard_last_admin(
    db: DbSession, user: User, *, new_role: str | None, context_user_id: str
) -> None:
    """Refuse a change that would leave the helpdesk with no active admin."""
    if user.role != "admin":
        return
    remaining = (
        db.query(func.count(User.id))
        .filter(User.role == "admin", User.is_active.is_(True), User.id != user.id)
        .scalar()
        or 0
    )
    if remaining == 0 and new_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This is the only active admin; promote someone else first",
        )


@router.get("/audit", response_model=list[AuditEntryOut])
def list_audit(
    db: DbSession,
    context: AdminUser,
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
) -> list[AuditLog]:
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action.startswith(action[:60]))
    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()


@router.get("/mail/health", response_model=MailHealthOut)
def mail_health(db: DbSession, context: AdminUser) -> MailHealthOut:
    """What the mail plumbing is doing, without exposing any credentials."""
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    queued = (
        db.query(func.count(OutboundEmail.id))
        .filter(OutboundEmail.status.in_(("queued", "sending")))
        .scalar()
        or 0
    )
    failed = (
        db.query(func.count(OutboundEmail.id))
        .filter(OutboundEmail.status == "failed")
        .scalar()
        or 0
    )
    sent = (
        db.query(func.count(OutboundEmail.id))
        .filter(OutboundEmail.status == "sent", OutboundEmail.sent_at >= day_ago)
        .scalar()
        or 0
    )
    last_inbound = db.execute(
        select(func.max(ProcessedInboundEmail.processed_at))
    ).scalar()

    return MailHealthOut(
        outbound_configured=settings.outbound_email_configured,
        inbound_enabled=settings.inbound_email_enabled,
        queued=int(queued),
        failed=int(failed),
        sent_last_24h=int(sent),
        last_inbound_at=last_inbound,
    )
