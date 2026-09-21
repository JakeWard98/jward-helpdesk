"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DbSession) -> dict[str, str]:
    """Unauthenticated on purpose - the container healthcheck calls it.

    It reports only liveness facts; nothing here identifies the deployment or
    leaks configuration.
    """
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - report, never raise, from a healthcheck
        database = "error"
    return {"status": "ok" if database == "ok" else "degraded", "database": database,
            "version": __version__}
