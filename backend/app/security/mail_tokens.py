"""Signed reply addresses and subject tags.

Every outbound mail carries a ``Reply-To`` of the form::

    helpdesk+t.1042.9f3c1ab27d0e4a56@example.com
             ^^ ^^^^ ^^^^^^^^^^^^^^^^
             |  |    HMAC(app secret, "1042:<ticket.reply_token>")
             |  ticket number
             tag marker

so a reply can be routed to the right ticket without trusting the sender, and
without letting anyone forge a reply address for a ticket they cannot see.
Threading headers are still used as a fallback; this is just the strongest
signal available.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from app.security.crypto import constant_time_equals, mail_token_key

SIGNATURE_LENGTH = 16
_PLUS_RE = re.compile(rf"\+t\.(\d+)\.([0-9a-f]{{{SIGNATURE_LENGTH}}})", re.IGNORECASE)
_SUBJECT_RE = re.compile(r"\[TKT-(\d+)\]", re.IGNORECASE)


def sign(ticket_number: int, reply_token: str) -> str:
    mac = hmac.new(
        mail_token_key(), f"{ticket_number}:{reply_token}".encode(), sha256
    ).hexdigest()
    return mac[:SIGNATURE_LENGTH]


def verify(ticket_number: int, reply_token: str, signature: str) -> bool:
    return constant_time_equals(sign(ticket_number, reply_token), (signature or "").lower())


def reply_address(base_address: str, ticket_number: int, reply_token: str) -> str:
    """Build the plus-addressed Reply-To for a ticket.

    Falls back to the bare address when the mailbox has no local part to
    extend (which only happens on a misconfiguration).
    """
    if "@" not in base_address:
        return base_address
    local, domain = base_address.rsplit("@", 1)
    return f"{local}+t.{ticket_number}.{sign(ticket_number, reply_token)}@{domain}"


def parse_recipients(addresses: list[str]) -> tuple[int, str] | None:
    """Find ``(ticket_number, signature)`` in any To/Cc/Delivered-To address."""
    for address in addresses:
        match = _PLUS_RE.search(address or "")
        if match:
            return int(match.group(1)), match.group(2).lower()
    return None


def parse_subject(subject: str) -> int | None:
    """Find a ``[TKT-123]`` tag in a subject line."""
    match = _SUBJECT_RE.search(subject or "")
    return int(match.group(1)) if match else None


def tag_subject(subject: str, ticket_number: int) -> str:
    """Ensure the subject carries exactly one ticket tag."""
    tag = f"[TKT-{ticket_number}]"
    if _SUBJECT_RE.search(subject or ""):
        return subject
    return f"{tag} {subject}".strip()
