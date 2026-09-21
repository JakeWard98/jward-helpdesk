"""Ticket templates: the default set, and validation of submitted answers."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Template

log = logging.getLogger(__name__)

FIELD_TYPES = ("text", "textarea", "select", "checkbox", "date", "number", "email")

# The fallback template is what an emailed-in ticket gets. It is deliberately
# almost empty - an agent picks the real template in the GUI afterwards and
# the answers get filled in then.
DEFAULT_TEMPLATES: list[dict[str, Any]] = [
    {
        "slug": "general-request",
        "name": "General request",
        "description": "Anything that does not fit another form.",
        "icon": "inbox",
        "is_fallback": True,
        "sort_order": 10,
        "default_priority": "normal",
        "fields": [],
    },
    {
        "slug": "it-support",
        "name": "IT support",
        "description": "Something is broken or behaving oddly.",
        "icon": "wrench",
        "sort_order": 20,
        "default_priority": "normal",
        "subject_prefix": "",
        "fields": [
            {
                "key": "device",
                "label": "Device",
                "type": "select",
                "required": True,
                "options": ["Desktop", "Laptop", "Phone", "Tablet", "Printer", "Other"],
            },
            {
                "key": "asset_tag",
                "label": "Asset tag or hostname",
                "type": "text",
                "required": False,
                "help": "Found on the sticker on the machine, if there is one.",
            },
            {
                "key": "started_at",
                "label": "When did it start?",
                "type": "date",
                "required": False,
            },
            {
                "key": "steps",
                "label": "What were you doing when it happened?",
                "type": "textarea",
                "required": True,
            },
        ],
    },
    {
        "slug": "account-access",
        "name": "Account or access",
        "description": "Password resets, MFA problems, permissions.",
        "icon": "key",
        "sort_order": 30,
        "default_priority": "high",
        "fields": [
            {
                "key": "system",
                "label": "Which system?",
                "type": "text",
                "required": True,
            },
            {
                "key": "access_type",
                "label": "What do you need?",
                "type": "select",
                "required": True,
                "options": ["Password reset", "MFA reset", "New account", "Extra permissions",
                            "Account locked", "Other"],
            },
            {
                "key": "manager_approved",
                "label": "Approved by your manager",
                "type": "checkbox",
                "required": False,
            },
        ],
    },
    {
        "slug": "new-hardware",
        "name": "Hardware or software request",
        "description": "Ask for a new device, peripheral or licence.",
        "icon": "package",
        "sort_order": 40,
        "default_priority": "low",
        "fields": [
            {"key": "item", "label": "What do you need?", "type": "text", "required": True},
            {"key": "quantity", "label": "Quantity", "type": "number", "required": False},
            {"key": "needed_by", "label": "Needed by", "type": "date", "required": False},
            {"key": "justification", "label": "Why is it needed?", "type": "textarea",
             "required": True},
        ],
    },
    {
        "slug": "incident",
        "name": "Outage or incident",
        "description": "Something is down and affecting more than one person.",
        "icon": "alert",
        "sort_order": 50,
        "default_priority": "urgent",
        "subject_prefix": "[Incident]",
        "fields": [
            {"key": "service", "label": "Affected service", "type": "text", "required": True},
            {
                "key": "impact",
                "label": "Who is affected?",
                "type": "select",
                "required": True,
                "options": ["Just me", "My team", "A whole site", "Everyone"],
            },
            {"key": "detail", "label": "What is happening?", "type": "textarea", "required": True},
        ],
    },
]


def seed_defaults(db: Session) -> int:
    """Insert the default templates that are missing. Never overwrites edits."""
    existing = {slug for (slug,) in db.query(Template.slug).all()}
    created = 0
    for spec in DEFAULT_TEMPLATES:
        if spec["slug"] in existing:
            continue
        db.add(Template(**spec))
        created += 1
    if created:
        db.flush()
        log.info("seeded %s default template(s)", created)
    return created


def get_fallback(db: Session) -> Template | None:
    """The template mail-in tickets are created with."""
    fallback = (
        db.query(Template)
        .filter(Template.is_fallback.is_(True), Template.is_active.is_(True))
        .order_by(Template.sort_order)
        .first()
    )
    if fallback:
        return fallback
    return (
        db.query(Template)
        .filter(Template.is_active.is_(True))
        .order_by(Template.sort_order)
        .first()
    )


def normalise_field_spec(fields: Any) -> list[dict[str, Any]]:
    """Validate an admin-supplied field list, raising ValueError on nonsense."""
    if not isinstance(fields, list):
        raise ValueError("fields must be a list")
    if len(fields) > 40:
        raise ValueError("a template can have at most 40 fields")

    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for raw in fields:
        if not isinstance(raw, dict):
            raise ValueError("each field must be an object")
        key = str(raw.get("key", "")).strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"invalid field key: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate field key: {key}")
        seen.add(key)

        field_type = str(raw.get("type", "text")).strip()
        if field_type not in FIELD_TYPES:
            raise ValueError(f"unsupported field type: {field_type}")

        options = raw.get("options") or []
        if field_type == "select":
            options = [str(o)[:120] for o in options if str(o).strip()]
            if not options:
                raise ValueError(f"field {key} is a select but has no options")

        cleaned.append(
            {
                "key": key[:60],
                "label": str(raw.get("label") or key)[:160],
                "type": field_type,
                "required": bool(raw.get("required")),
                "options": options,
                "help": str(raw.get("help") or "")[:300],
            }
        )
    return cleaned


def validate_answers(template: Template | None, answers: dict[str, Any]) -> dict[str, Any]:
    """Check submitted answers against the template and drop unknown keys."""
    if template is None:
        return {}
    answers = answers or {}
    result: dict[str, Any] = {}
    errors: list[str] = []

    for spec in template.fields or []:
        key = spec.get("key")
        if not key:
            continue
        value = answers.get(key)
        field_type = spec.get("type", "text")

        if value in (None, "", []):
            if spec.get("required"):
                errors.append(f"{spec.get('label', key)} is required")
            continue

        if field_type == "checkbox":
            value = bool(value)
        elif field_type == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                errors.append(f"{spec.get('label', key)} must be a number")
                continue
        elif field_type == "select":
            value = str(value)
            if value not in (spec.get("options") or []):
                errors.append(f"{spec.get('label', key)} is not one of the allowed options")
                continue
        else:
            value = str(value)[:4000]

        result[key] = value

    if errors:
        raise ValueError("; ".join(errors))
    return result


def render_field_summary(template: Template | None, answers: dict[str, Any]) -> str:
    """Plain-text block appended to the first message of a templated ticket."""
    if not template or not answers:
        return ""
    lines = []
    for spec in template.fields or []:
        key = spec.get("key")
        if key in answers:
            value = answers[key]
            if isinstance(value, bool):
                value = "Yes" if value else "No"
            lines.append(f"{spec.get('label', key)}: {value}")
    return "\n".join(lines)
