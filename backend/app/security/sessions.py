"""Server-side session handling.

The browser only ever holds an opaque random token; everything meaningful
lives in the ``auth_sessions`` row, so a session can be revoked instantly and
an attacker who reads a cookie cannot learn anything from it.

Cookies use the ``__Host-`` prefix in production, which the browser only
accepts when the cookie is Secure, Path=/ and has no Domain attribute - that
rules out a subdomain of the tunnel hostname setting a session for us.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, User
from app.security.crypto import new_token, sha256_hex
from app.utils import as_utc, now_utc

SESSION_COOKIE = "__Host-hd_session" if settings.session_cookie_secure else "hd_session"
CSRF_COOKIE = "__Host-hd_csrf" if settings.session_cookie_secure else "hd_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _cookie_kwargs() -> dict[str, object]:
    return {
        "secure": settings.session_cookie_secure,
        "samesite": "strict",
        "path": "/",
    }


def create_session(
    db: Session,
    user: User,
    *,
    request: Request,
    mfa_satisfied: bool,
) -> tuple[AuthSession, str]:
    """Create a session row and return it with the raw (unhashed) token."""
    raw_token = new_token(32)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=sha256_hex(raw_token),
        csrf_token=new_token(24),
        mfa_satisfied=mfa_satisfied,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
        expires_at=now_utc() + timedelta(hours=settings.session_absolute_timeout_hours),
    )
    db.add(auth_session)
    db.flush()
    return auth_session, raw_token


def attach_cookies(response: Response, auth_session: AuthSession, raw_token: str) -> None:
    max_age = int((as_utc(auth_session.expires_at) - now_utc()).total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        max_age=max_age,
        **_cookie_kwargs(),
    )
    # Readable by JavaScript on purpose: the SPA echoes it back in a header,
    # which is what proves the request did not come from another origin.
    response.set_cookie(
        CSRF_COOKIE,
        auth_session.csrf_token,
        httponly=False,
        max_age=max_age,
        **_cookie_kwargs(),
    )


def clear_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def load_session(db: Session, request: Request) -> AuthSession | None:
    """Return the live session for this request, sliding its idle window."""
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return None

    auth_session = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == sha256_hex(raw_token))
        .one_or_none()
    )
    if auth_session is None or auth_session.revoked_at is not None:
        return None

    now = now_utc()
    if as_utc(auth_session.expires_at) <= now:
        return None

    last_seen = as_utc(auth_session.last_seen_at)
    idle_limit = timedelta(minutes=settings.session_idle_timeout_minutes)
    if now - last_seen > idle_limit:
        auth_session.revoked_at = now
        db.flush()
        return None

    # Only write on a meaningful move, to keep login traffic from hammering
    # the row on every poll.
    if now - last_seen > timedelta(seconds=60):
        auth_session.last_seen_at = now
        db.flush()
    return auth_session


def rotate_token(db: Session, auth_session: AuthSession) -> str:
    """Issue a fresh cookie token and CSRF token for an existing session.

    Called whenever the session gains privilege (after MFA, after a password
    change), so a token captured before that point becomes useless - the
    standard defence against session fixation.
    """
    raw_token = new_token(32)
    auth_session.token_hash = sha256_hex(raw_token)
    auth_session.csrf_token = new_token(24)
    db.flush()
    return raw_token


def revoke(db: Session, auth_session: AuthSession) -> None:
    auth_session.revoked_at = now_utc()
    db.flush()


def revoke_all_for_user(db: Session, user_id: str, *, except_id: str | None = None) -> int:
    query = db.query(AuthSession).filter(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_id:
        query = query.filter(AuthSession.id != except_id)
    now = now_utc()
    count = 0
    for row in query.all():
        row.revoked_at = now
        count += 1
    db.flush()
    return count


def purge_expired(db: Session) -> int:
    cutoff = now_utc() - timedelta(days=7)
    deleted = (
        db.query(AuthSession)
        .filter(AuthSession.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    ``CF-Connecting-IP`` is only trusted when TRUST_CLOUDFLARE_HEADERS is on,
    which is only correct when the tunnel is the single path to the app. On a
    directly reachable deployment the header is attacker-controlled, and
    trusting it would let anyone dodge the login rate limiter.
    """
    if settings.trust_cloudflare_headers:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()[:64]
    return (request.client.host if request.client else "")[:64]
