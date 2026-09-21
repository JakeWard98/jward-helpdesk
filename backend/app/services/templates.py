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
        "name": "Something else",
        "description": "Anything that does not fit the other forms.",
        "icon": "inbox",
        "is_fallback": True,
        "sort_order": 10,
        "default_priority": "normal",
        "fields": [],
    },
    {
        "slug": "device-problem",
        "name": "Something is not working",
        "description": "A device or app that has stopped behaving.",
        "icon": "wrench",
        "sort_order": 20,
        "default_priority": "normal",
        "fields": [
            {
                "key": "device",
                "label": "What is it?",
                "type": "select",
                "required": True,
                "options": [
                    "Laptop",
                    "Desktop",
                    "Phone",
                    "Tablet",
                    "TV",
                    "Games console",
                    "Printer",
                    "Smart home device",
                    "Something else",
                ],
            },
            {
                "key": "what_happened",
                "label": "What happens when you try?",
                "type": "textarea",
                "required": True,
                "help": "Any error message, word for word, helps a lot.",
            },
            {
                "key": "started",
                "label": "When did it start?",
                "type": "date",
                "required": False,
            },
        ],
    },
    {
        "slug": "internet-wifi",
        "name": "Internet or Wi-Fi",
        "description": "Nothing loads, or it keeps dropping out.",
        "icon": "wifi",
        "sort_order": 30,
        "default_priority": "high",
        "fields": [
            {
                "key": "symptom",
                "label": "What is it doing?",
                "type": "select",
                "required": True,
                "options": [
                    "No connection at all",
                    "Very slow",
                    "Keeps dropping out",
                    "One site or app will not load",
                ],
            },
            {
                "key": "scope",
                "label": "What is affected?",
                "type": "select",
                "required": True,
                "options": ["Just this device", "Everything in one room", "The whole house"],
            },
            {
                "key": "where",
                "label": "Which room?",
                "type": "text",
                "required": False,
            },
        ],
    },
    {
        "slug": "media-streaming",
        "name": "Media or streaming",
        "description": "Films, music, photos or the media server.",
        "icon": "play",
        "sort_order": 40,
        "default_priority": "low",
        "fields": [
            {
                "key": "service",
                "label": "Which app or service?",
                "type": "text",
                "required": True,
                "help": "For example Plex, Jellyfin, or the TV app you were using.",
            },
            {
                "key": "what_happened",
                "label": "What went wrong?",
                "type": "textarea",
                "required": True,
            },
        ],
    },
    {
        "slug": "account-help",
        "name": "Login or account help",
        "description": "Passwords, being locked out, or setting something up.",
        "icon": "key",
        "sort_order": 50,
        "default_priority": "normal",
        "fields": [
            {
                "key": "service",
                "label": "Which account?",
                "type": "text",
                "required": True,
            },
            {
                "key": "problem",
                "label": "What do you need?",
                "type": "select",
                "required": True,
                "options": [
                    "Forgotten password",
                    "Locked out",
                    "Needs setting up",
                    "Something else",
                ],
            },
        ],
    },
    {
        "slug": "request",
        "name": "Ask for something",
        "description": "A new device, an app, or something set up.",
        "icon": "package",
        "sort_order": 60,
        "default_priority": "low",
        "fields": [
            {
                "key": "what",
                "label": "What would you like?",
                "type": "text",
                "required": True,
            },
            {
                "key": "details",
                "label": "Any detail that helps",
                "type": "textarea",
                "required": False,
            },
            {
                "key": "needed_by",
                "label": "Needed by",
                "type": "date",
                "required": False,
            },
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
