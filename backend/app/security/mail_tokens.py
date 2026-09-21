"""Signed ticket references carried in the subject line and the message body.

Nothing is encoded into the email *address* - plenty of providers (Proton
among them) will not carry a plus-addressed local part reliably, and the
address is the one part of an email a forwarding rule is most likely to
rewrite. Instead every outbound message carries the same signed reference
twice:

    Subject: [TKT-1042-9f3c1ab27d0e] Laptop will not boot
    ...
    [ref:1042-9f3c1ab27d0e]

where the signature is ``HMAC(app secret, "<number>:<ticket.reply_token>")``.
``reply_token`` is 16 random bytes stored on the ticket, so one ticket's
reference tells you nothing about another's, and neither can be forged
without ``APP_SECRET``.

Two copies because mail clients damage different things: a subject can be
rewritten by the sender ("changing the subject" mid-thread), while the body
reference survives in the quoted history of almost any reply. If both are
gone, threading headers are still tried - see ``services/email_inbound``.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from app.security.crypto import constant_time_equals, mail_token_key

# 48 bits. Short enough to live in a subject line without being an eyesore,
# far beyond guessing at the rate a mailbox can be fed.
SIGNATURE_LENGTH = 12

_SUBJECT_RE = re.compile(
    rf"\[TKT-(\d+)(?:-([0-9a-f]{{{SIGNATURE_LENGTH}}}))?\]", re.IGNORECASE
)
_BODY_RE = re.compile(
    rf"\[ref:(\d+)-([0-9a-f]{{{SIGNATURE_LENGTH}}})\]", re.IGNORECASE
)


def sign(ticket_number: int, reply_token: str) -> str:
    mac = hmac.new(
        mail_token_key(), f"{ticket_number}:{reply_token}".encode(), sha256
    ).hexdigest()
    return mac[:SIGNATURE_LENGTH]


def verify(ticket_number: int, reply_token: str, signature: str) -> bool:
    return constant_time_equals(sign(ticket_number, reply_token), (signature or "").lower())


# ---------------------------------------------------------------------------
# subject line
# ---------------------------------------------------------------------------
def subject_tag(ticket_number: int, reply_token: str) -> str:
    return f"[TKT-{ticket_number}-{sign(ticket_number, reply_token)}]"


def tag_subject(subject: str, ticket_number: int, reply_token: str) -> str:
    """Ensure the subject carries exactly one signed ticket tag.

    An existing tag is replaced rather than appended to, so a reply whose
    subject already carries the tag does not accumulate copies.
    """
    subject = (subject or "").strip()
    tag = subject_tag(ticket_number, reply_token)
    if _SUBJECT_RE.search(subject):
        return _SUBJECT_RE.sub(tag, subject, count=1)
    return f"{tag} {subject}".strip()


def parse_subject(subject: str) -> tuple[int, str | None] | None:
    """Find a ticket tag in a subject line.

    Returns ``(ticket_number, signature_or_None)``. An unsigned tag is still
    reported, because it is a useful hint - but the caller must treat it as
    unverified and fall back to checking the sender.
    """
    match = _SUBJECT_RE.search(subject or "")
    if not match:
        return None
    signature = match.group(2)
    return int(match.group(1)), signature.lower() if signature else None


# ---------------------------------------------------------------------------
# message body
# ---------------------------------------------------------------------------
def body_reference(ticket_number: int, reply_token: str) -> str:
    return f"[ref:{ticket_number}-{sign(ticket_number, reply_token)}]"


def parse_body(*bodies: str | None) -> tuple[int, str] | None:
    """Find a signed reference anywhere in the given bodies.

    The *raw* body is searched, before quoted history is trimmed, because the
    reference usually survives precisely in that quoted history.
    """
    for body in bodies:
        match = _BODY_RE.search(body or "")
        if match:
            return int(match.group(1)), match.group(2).lower()
    return None
