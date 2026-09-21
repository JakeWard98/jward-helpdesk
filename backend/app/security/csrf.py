"""CSRF protection for cookie-authenticated requests."""

from __future__ import annotations

from fastapi import Request

from app.models import AuthSession
from app.security.crypto import constant_time_equals
from app.security.sessions import CSRF_HEADER

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_valid(request: Request, auth_session: AuthSession) -> bool:
    """Synchroniser-token check.

    The token lives in the session row, so a stolen cookie value alone is not
    enough; the caller has to be able to read our cookie, which cross-origin
    JavaScript cannot do.
    """
    if request.method.upper() in SAFE_METHODS:
        return True
    supplied = request.headers.get(CSRF_HEADER, "")
    if not supplied:
        return False
    return constant_time_equals(auth_session.csrf_token, supplied)
