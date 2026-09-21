"""SQLAlchemy engine, session factory and schema bootstrap."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for background work; commits on success, rolls back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create tables and the ticket-number sequence if they do not exist.

    The schema is small and additive so far, so ``create_all`` is enough. Once
    a column has to change shape, add Alembic rather than editing tables by
    hand - see CLAUDE.md.
    """
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("CREATE SEQUENCE IF NOT EXISTS ticket_number_seq START WITH 1000"))
    log.info("database schema ready")


def next_ticket_number(db: Session) -> int:
    """Allocate the next human-facing ticket number.

    A sequence rather than ``max(number) + 1`` so two concurrent inbound mails
    can never be handed the same ticket number.
    """
    if db.bind is not None and db.bind.dialect.name != "postgresql":
        # Test/SQLite fallback only; not safe under concurrency.
        from app.models import Ticket

        highest = db.query(func.max(Ticket.number)).scalar() or 999
        return int(highest) + 1
    return int(db.execute(text("SELECT nextval('ticket_number_seq')")).scalar_one())
