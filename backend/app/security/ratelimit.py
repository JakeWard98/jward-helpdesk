"""Database-backed fixed-window rate limiting.

Shared across API workers and restarts, which an in-process counter would not
be. Buckets are keyed by action plus client IP, or action plus account, so one
noisy client cannot lock out everybody else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import RateLimitBucket
from app.utils import as_utc, now_utc


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


def hit(
    db: Session,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    block_seconds: int = 0,
) -> RateLimitResult:
    """Count one attempt against ``key``.

    Uses ``SELECT ... FOR UPDATE`` so two simultaneous requests cannot both
    slip through on the last remaining slot.
    """
    now = now_utc()
    bucket = (
        db.query(RateLimitBucket)
        .filter(RateLimitBucket.key == key)
        .with_for_update()
        .one_or_none()
    )

    if bucket is None:
        bucket = RateLimitBucket(key=key, window_started_at=now, count=1)
        db.add(bucket)
        db.flush()
        return RateLimitResult(allowed=True, remaining=limit - 1)

    blocked_until = as_utc(bucket.blocked_until)
    if blocked_until and blocked_until > now:
        return RateLimitResult(
            allowed=False,
            retry_after_seconds=int((blocked_until - now).total_seconds()) + 1,
        )

    if (now - as_utc(bucket.window_started_at)).total_seconds() >= window_seconds:
        bucket.window_started_at = now
        bucket.count = 1
        bucket.blocked_until = None
        db.flush()
        return RateLimitResult(allowed=True, remaining=limit - 1)

    bucket.count += 1
    if bucket.count > limit:
        retry = block_seconds or window_seconds
        bucket.blocked_until = now + timedelta(seconds=retry)
        db.flush()
        return RateLimitResult(allowed=False, retry_after_seconds=retry)

    db.flush()
    return RateLimitResult(allowed=True, remaining=max(0, limit - bucket.count))


def reset(db: Session, key: str) -> None:
    """Clear a bucket, e.g. after a successful login."""
    db.query(RateLimitBucket).filter(RateLimitBucket.key == key).delete()


def purge_expired(db: Session, older_than_seconds: int = 86400) -> int:
    cutoff = now_utc() - timedelta(seconds=older_than_seconds)
    result = db.execute(
        delete(RateLimitBucket).where(RateLimitBucket.window_started_at < cutoff)
    )
    return int(result.rowcount or 0)
