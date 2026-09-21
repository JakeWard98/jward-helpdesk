"""First-run setup: schema, default templates and the initial admin."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, session_scope
from app.models import User
from app.security import passwords
from app.services import audit, templates

log = logging.getLogger(__name__)


def ensure_bootstrap_admin(db: Session) -> None:
    """Create the first admin, once.

    Only ever runs when no admin exists, so leaving BOOTSTRAP_ADMIN_* in the
    stack environment cannot silently reset a live account. The password is
    marked as needing a change at first login.
    """
    existing_admin = db.query(User).filter(User.role == "admin").first()
    if existing_admin:
        return

    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if not email or not password:
        log.warning(
            "no admin account exists and BOOTSTRAP_ADMIN_EMAIL/PASSWORD are unset - "
            "set them and restart to create the first login"
        )
        return

    problems = passwords.password_problems(password, email=email)
    if problems:
        log.error("refusing to create the bootstrap admin: password %s", ", ".join(problems))
        return

    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)

    user.display_name = user.display_name or email.split("@")[0]
    user.role = "admin"
    user.is_active = True
    user.password_hash = passwords.hash_password(password)
    user.must_change_password = True
    db.flush()

    audit.record(
        db,
        action="user.bootstrap_admin_created",
        actor_label="bootstrap",
        object_type="user",
        object_id=user.id,
        detail={"email": email},
    )
    log.warning(
        "created bootstrap admin %s - sign in, change the password, enrol MFA, "
        "then clear BOOTSTRAP_ADMIN_PASSWORD from the environment",
        email,
    )


def run() -> None:
    """Idempotent start-up sequence."""
    init_db()
    with session_scope() as db:
        templates.seed_defaults(db)
        ensure_bootstrap_admin(db)
