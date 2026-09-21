"""FastAPI dependencies: authentication, authorisation and CSRF."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthSession, User
from app.security import csrf, sessions

DbSession = Annotated[Session, Depends(get_db)]


class AuthContext:
    """The authenticated caller, for the life of one request."""

    __slots__ = ("user", "session")

    def __init__(self, user: User, session: AuthSession) -> None:
        self.user = user
        self.session = session


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not signed in",
        headers={"WWW-Authenticate": "Cookie"},
    )


def get_partial_auth(request: Request, db: DbSession) -> AuthContext:
    """A session that exists but may not have cleared MFA yet.

    Only the MFA endpoints accept this; everything else requires
    :func:`get_current_user`.
    """
    auth_session = sessions.load_session(db, request)
    if auth_session is None:
        raise _unauthenticated()

    user = db.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        sessions.revoke(db, auth_session)
        raise _unauthenticated()

    if not csrf.is_valid(request, auth_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid"
        )

    return AuthContext(user=user, session=auth_session)


def get_current_user(
    context: Annotated[AuthContext, Depends(get_partial_auth)],
) -> AuthContext:
    """A fully authenticated caller: password *and*, where required, MFA."""
    if not context.session.mfa_satisfied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Multi-factor authentication required",
        )
    if context.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    return context


CurrentUser = Annotated[AuthContext, Depends(get_current_user)]
PartialUser = Annotated[AuthContext, Depends(get_partial_auth)]


def require_role(*roles: str) -> Callable[[AuthContext], AuthContext]:
    allowed = set(roles)

    def _dependency(context: CurrentUser) -> AuthContext:
        if context.user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this",
            )
        return context

    return _dependency


StaffUser = Annotated[AuthContext, Depends(require_role("admin", "agent"))]
AdminUser = Annotated[AuthContext, Depends(require_role("admin"))]
