"""Append-only audit trail.

Recorded for every authentication event and every change an agent makes, so a
compromise can be reconstructed afterwards. Never log a password, TOTP code,
session token or full mail body here.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User

log = logging.getLogger("helpdesk.audit")

REDACTED_KEYS = {"password", "code", "token", "secret", "totp"}


def _scrub(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        k: ("[redacted]" if any(marker in k.lower() for marker in REDACTED_KEYS) else v)
        for k, v in (detail or {}).items()
    }


def record(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    actor_label: str = "",
    object_type: str = "",
    object_id: str = "",
    ip_address: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_label=actor_label or (actor.email if actor else "system"),
        action=action,
        object_type=object_type,
        object_id=str(object_id or ""),
        ip_address=ip_address,
        detail=_scrub(detail or {}),
    )
    db.add(entry)
    log.info(
        "audit action=%s actor=%s object=%s/%s ip=%s",
        action,
        entry.actor_label,
        object_type,
        object_id,
        ip_address or "-",
    )
    return entry
