"""Small helpers shared across the app."""

from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise a database timestamp to an aware UTC datetime.

    Postgres ``timestamptz`` comes back aware, but other drivers (and SQLite in
    the test suite) hand back naive values. Comparing the two raises, so every
    stored timestamp goes through here before it meets ``now_utc()``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
