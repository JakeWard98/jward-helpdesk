"""Test fixtures.

The suite runs against a throwaway SQLite database so it needs no containers.
Everything exercised here is dialect-agnostic; the Postgres-only pieces (the
ticket-number sequence, JSONB) have documented fallbacks in database.py and
models.py.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

TMP_ROOT = Path(tempfile.mkdtemp(prefix="helpdesk-tests-"))

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APP_SECRET", "test-secret-please-ignore-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", f"sqlite+pysqlite:///{TMP_ROOT / 'test.db'}")
os.environ.setdefault("ATTACHMENT_DIR", str(TMP_ROOT / "attachments"))
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost:8080")
os.environ.setdefault("SMTP_HOST", "smtp.test.invalid")
os.environ.setdefault("SMTP_FROM_EMAIL", "helpdesk@test.invalid")
os.environ.setdefault("SMTP_REPLY_TO_EMAIL", "helpdesk@test.invalid")
os.environ.setdefault("SMTP_FROM_NAME", "Test Helpdesk")

from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import User  # noqa: E402
from app.services import templates as template_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db(_schema: None) -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        # Leave the database empty for the next test.
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture()
def templates(db: Session) -> None:
    template_service.seed_defaults(db)
    db.commit()


@pytest.fixture()
def agent(db: Session) -> User:
    user = User(
        email="agent@test.invalid",
        display_name="Test Agent",
        role="agent",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user
