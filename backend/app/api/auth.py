"""Authentication: password login, TOTP MFA, sessions, password changes."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import settings
from app.deps import CurrentUser, DbSession, PartialUser
from app.models import RecoveryCode, User
from app.schemas import (
    LoginRequest,
    LoginResponse,
    MfaChallengeRequest,
    MfaEnrolConfirmRequest,
    MfaEnrolConfirmResponse,
    MfaEnrolStartResponse,
    PasswordChangeRequest,
    UserOut,
)
from app.security import passwords, ratelimit, sessions, totp
from app.security.crypto import decrypt_secret, encrypt_secret
from app.services import audit
from app.utils import as_utc, now_utc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Email or password is incorrect"


def _mfa_required_for(user: User) -> bool:
    """Staff must use MFA when the deployment says so; anyone may opt in."""
    return user.mfa_enabled or (user.is_staff and settings.require_mfa_for_agents)


def _check_rate_limits(db: DbSession, request: Request, email: str) -> None:
    """Throttle by IP and by account.

    Both, because one attacker trying many accounts and many attackers trying
    one account are different attacks and each needs its own ceiling.
    """
    ip = sessions.client_ip(request)
    for key in (f"login:ip:{ip}", f"login:user:{email.lower()}"):
        result = ratelimit.hit(
            db,
            key,
            limit=settings.login_max_attempts,
            window_seconds=settings.login_window_seconds,
            block_seconds=settings.login_lockout_seconds,
        )
        if not result.allowed:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Try again later.",
                headers={"Retry-After": str(result.retry_after_seconds)},
            )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> LoginResponse:
    email = payload.email.lower().strip()
    _check_rate_limits(db, request, email)

    user = db.query(User).filter(User.email == email).one_or_none()
    now = now_utc()

    # Always run the hash comparison, even for an unknown account, so the
    # response time does not reveal which addresses exist.
    password_ok = passwords.verify_password(user.password_hash if user else None, payload.password)

    if user is None or not password_ok or not user.is_active:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_attempts:
                user.locked_until = now + timedelta(seconds=settings.login_lockout_seconds)
        audit.record(
            db,
            action="auth.login_failed",
            actor=user,
            actor_label=email,
            ip_address=sessions.client_ip(request),
            detail={"reason": "bad_credentials" if user else "unknown_account"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    locked_until = as_utc(user.locked_until)
    if locked_until and locked_until > now:
        audit.record(
            db,
            action="auth.login_blocked",
            actor=user,
            ip_address=sessions.client_ip(request),
            detail={"reason": "locked"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="This account is temporarily locked. Try again later.",
        )

    # Credentials accepted from here on.
    user.failed_login_count = 0
    user.locked_until = None
    if passwords.needs_rehash(user.password_hash or ""):
        user.password_hash = passwords.hash_password(payload.password)

    needs_mfa = _mfa_required_for(user)
    auth_session, raw_token = sessions.create_session(
        db, user, request=request, mfa_satisfied=not needs_mfa
    )

    if not needs_mfa:
        user.last_login_at = now

    ratelimit.reset(db, f"login:user:{email}")
    audit.record(
        db,
        action="auth.login",
        actor=user,
        ip_address=sessions.client_ip(request),
        detail={"mfa_pending": needs_mfa, "mfa_enrolled": user.mfa_enabled},
    )
    db.commit()

    sessions.attach_cookies(response, auth_session, raw_token)

    if needs_mfa:
        return LoginResponse(
            status="mfa_required" if user.mfa_enabled else "mfa_enrolment_required",
            csrf_token=auth_session.csrf_token,
        )
    return LoginResponse(
        status="authenticated",
        user=UserOut.model_validate(user),
        csrf_token=auth_session.csrf_token,
    )


@router.post("/mfa/verify", response_model=LoginResponse)
def verify_mfa(
    payload: MfaChallengeRequest,
    request: Request,
    response: Response,
    db: DbSession,
    context: PartialUser,
) -> LoginResponse:
    """Second factor. Accepts a TOTP code or a single-use recovery code."""
    user = context.user
    if context.session.mfa_satisfied:
        return LoginResponse(
            status="authenticated",
            user=UserOut.model_validate(user),
            csrf_token=context.session.csrf_token,
        )
    if not user.mfa_enabled or not user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not set up for this account"
        )

    ip = sessions.client_ip(request)
    limit = ratelimit.hit(
        db, f"mfa:{user.id}", limit=6, window_seconds=300, block_seconds=300
    )
    if not limit.allowed:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many codes tried. Try again shortly.",
            headers={"Retry-After": str(limit.retry_after_seconds)},
        )

    code = payload.code.strip()
    matched_counter = totp.verify_code(
        decrypt_secret(user.totp_secret_encrypted), code, last_counter=user.last_totp_counter
    )
    used_recovery = False

    if matched_counter is None:
        used_recovery = _consume_recovery_code(db, user, code)
        if not used_recovery:
            audit.record(
                db, action="auth.mfa_failed", actor=user, ip_address=ip, detail={}
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="That code is not valid"
            )
    else:
        # Remember the counter so the same code cannot be replayed.
        user.last_totp_counter = matched_counter

    context.session.mfa_satisfied = True
    user.last_login_at = now_utc()
    raw_token = sessions.rotate_token(db, context.session)
    ratelimit.reset(db, f"mfa:{user.id}")
    audit.record(
        db,
        action="auth.mfa_ok",
        actor=user,
        ip_address=ip,
        detail={"method": "recovery_code" if used_recovery else "totp"},
    )
    db.commit()

    sessions.attach_cookies(response, context.session, raw_token)
    return LoginResponse(
        status="authenticated",
        user=UserOut.model_validate(user),
        csrf_token=context.session.csrf_token,
    )


def _consume_recovery_code(db: DbSession, user: User, code: str) -> bool:
    code_hash = totp.hash_recovery_code(code)
    row = (
        db.query(RecoveryCode)
        .filter(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == code_hash,
            RecoveryCode.used_at.is_(None),
        )
        .one_or_none()
    )
    if row is None:
        return False
    row.used_at = now_utc()
    db.flush()
    return True


@router.post("/mfa/enrol/start", response_model=MfaEnrolStartResponse)
def start_mfa_enrolment(
    db: DbSession,
    context: PartialUser,
) -> MfaEnrolStartResponse:
    """Generate a TOTP secret.

    Reachable with a partial session so a staff account that is required to
    use MFA can enrol at first login. The secret is stored encrypted, and MFA
    only actually turns on once a code from it is confirmed.
    """
    user = context.user
    if user.mfa_enabled and context.session.mfa_satisfied is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled; sign in with your code instead",
        )

    secret = totp.generate_secret()
    user.totp_secret_encrypted = encrypt_secret(secret)
    db.commit()

    uri = totp.provisioning_uri(secret, account=user.email)
    return MfaEnrolStartResponse(secret=secret, otpauth_uri=uri, qr_svg=totp.qr_svg(uri))


@router.post("/mfa/enrol/confirm", response_model=MfaEnrolConfirmResponse)
def confirm_mfa_enrolment(
    payload: MfaEnrolConfirmRequest,
    request: Request,
    response: Response,
    db: DbSession,
    context: PartialUser,
) -> MfaEnrolConfirmResponse:
    user = context.user
    if not user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Start enrolment first"
        )

    matched = totp.verify_code(decrypt_secret(user.totp_secret_encrypted), payload.code)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code did not match. Check your authenticator's clock.",
        )

    user.mfa_enabled = True
    user.last_totp_counter = matched

    # Replace any previous recovery codes: old ones must stop working.
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
    codes = totp.generate_recovery_codes()
    for code in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(code)))

    context.session.mfa_satisfied = True
    raw_token = sessions.rotate_token(db, context.session)
    audit.record(
        db,
        action="auth.mfa_enrolled",
        actor=user,
        ip_address=sessions.client_ip(request),
        detail={"recovery_codes": len(codes)},
    )
    db.commit()

    sessions.attach_cookies(response, context.session, raw_token)
    return MfaEnrolConfirmResponse(recovery_codes=codes)


@router.post("/mfa/recovery-codes", response_model=MfaEnrolConfirmResponse)
def regenerate_recovery_codes(
    request: Request,
    db: DbSession,
    context: CurrentUser,
) -> MfaEnrolConfirmResponse:
    user = context.user
    if not user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled"
        )
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
    codes = totp.generate_recovery_codes()
    for code in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(code)))
    audit.record(
        db,
        action="auth.recovery_codes_regenerated",
        actor=user,
        ip_address=sessions.client_ip(request),
    )
    db.commit()
    return MfaEnrolConfirmResponse(recovery_codes=codes)


@router.post("/password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    db: DbSession,
    context: PartialUser,
) -> dict[str, str]:
    """Change your own password.

    Uses the partial dependency because a user flagged ``must_change_password``
    is blocked from every other authenticated endpoint - this is the one they
    have to be able to reach. MFA, where enabled, is still enforced here.
    """
    user = context.user
    if not context.session.mfa_satisfied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Finish signing in first"
        )
    if not passwords.verify_password(user.password_hash, payload.current_password):
        audit.record(
            db,
            action="auth.password_change_failed",
            actor=user,
            ip_address=sessions.client_ip(request),
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )

    problems = passwords.password_problems(payload.new_password, email=user.email)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password " + ", ".join(problems),
        )

    user.password_hash = passwords.hash_password(payload.new_password)
    user.must_change_password = False

    # Any other session was established under the old password.
    revoked = sessions.revoke_all_for_user(db, user.id, except_id=context.session.id)
    raw_token = sessions.rotate_token(db, context.session)
    audit.record(
        db,
        action="auth.password_changed",
        actor=user,
        ip_address=sessions.client_ip(request),
        detail={"other_sessions_revoked": revoked},
    )
    db.commit()

    sessions.attach_cookies(response, context.session, raw_token)
    return {"status": "ok"}


@router.get("/me", response_model=LoginResponse)
def me(db: DbSession, context: PartialUser) -> LoginResponse:
    """Who am I, and what does the UI still need from me?"""
    user = context.user
    if not context.session.mfa_satisfied:
        return LoginResponse(
            status="mfa_required" if user.mfa_enabled else "mfa_enrolment_required",
            csrf_token=context.session.csrf_token,
        )
    return LoginResponse(
        status="authenticated",
        user=UserOut.model_validate(user),
        csrf_token=context.session.csrf_token,
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: DbSession,
    context: PartialUser,
) -> dict[str, str]:
    sessions.revoke(db, context.session)
    audit.record(
        db,
        action="auth.logout",
        actor=context.user,
        ip_address=sessions.client_ip(request),
    )
    db.commit()
    sessions.clear_cookies(response)
    return {"status": "ok"}


@router.post("/logout-everywhere")
def logout_everywhere(
    request: Request,
    response: Response,
    db: DbSession,
    context: CurrentUser,
) -> dict[str, int]:
    count = sessions.revoke_all_for_user(db, context.user.id)
    audit.record(
        db,
        action="auth.logout_all",
        actor=context.user,
        ip_address=sessions.client_ip(request),
        detail={"sessions_revoked": count},
    )
    db.commit()
    sessions.clear_cookies(response)
    return {"revoked": count}
