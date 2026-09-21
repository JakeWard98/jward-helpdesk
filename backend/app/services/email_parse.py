"""RFC 5322 parsing for inbound mail.

Pure functions over raw bytes - no database, no network - so the awkward parts
(multipart trees, quoted-printable, inline images, auto-replies) are testable
on their own. See tests/test_email_parse.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

log = logging.getLogger(__name__)

# Headers that mark a message as machine-generated. Replying to these creates
# mail loops, so such messages are logged against the ticket but never
# answered automatically.
AUTO_REPLY_HEADERS = {
    "auto-submitted": lambda v: v.lower() != "no",
    "x-auto-response-suppress": lambda v: True,
    "x-autoreply": lambda v: True,
    "x-autorespond": lambda v: True,
    "precedence": lambda v: v.lower() in {"bulk", "junk", "auto_reply", "list"},
}

# Common reply separators, used to trim the quoted history off a reply so the
# ticket timeline shows what the person actually wrote.
_QUOTE_MARKERS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^On .{5,120}\bwrote:\s*$", re.MULTILINE),
    re.compile(r"^From:\s.+$", re.MULTILINE),
    re.compile(r"^_{10,}\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Please reply above this line\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE),
]

REPLY_DELIMITER = "--- Please reply above this line ---"


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    data: bytes
    content_id: str | None = None
    is_inline: bool = False


@dataclass
class ParsedEmail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    from_name: str
    from_email: str
    to_addresses: list[str]
    cc_addresses: list[str]
    delivered_to: list[str]
    date: datetime | None
    text_body: str
    html_body: str | None
    attachments: list[ParsedAttachment] = field(default_factory=list)
    is_auto_reply: bool = False

    @property
    def all_recipients(self) -> list[str]:
        return [*self.to_addresses, *self.cc_addresses, *self.delivered_to]


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def _normalise_message_id(value: str | None) -> str | None:
    """Return a Message-ID in canonical ``<id@host>`` form."""
    if not value:
        return None
    match = re.search(r"<[^<>\s]+>", value)
    if match:
        return match.group(0)
    value = value.strip()
    return f"<{value}>" if value else None


def _extract_references(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^<>\s]+>", value)


def _addresses(message: Message, header: str) -> list[str]:
    raw = message.get_all(header, [])
    return [addr.lower() for _, addr in getaddresses(raw) if addr]


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _is_attachment(part: Message) -> bool:
    disposition = (part.get_content_disposition() or "").lower()
    if disposition == "attachment":
        return True
    # An inline part with a filename or a Content-ID is still a file we want.
    return bool(part.get_filename()) or bool(part.get("Content-ID"))


def detect_auto_reply(message: Message) -> bool:
    for header, predicate in AUTO_REPLY_HEADERS.items():
        value = message.get(header)
        if value and predicate(str(value)):
            return True
    subject = _decode_header_value(message.get("Subject")).lower()
    return subject.startswith(("auto:", "automatic reply", "out of office"))


def strip_quoted_reply(text: str) -> str:
    """Cut a reply down to its new content.

    Conservative on purpose: if trimming would leave almost nothing, the full
    body is kept instead of silently losing the customer's message.
    """
    if not text:
        return ""
    earliest = len(text)
    for pattern in _QUOTE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()

    candidate = text[:earliest].rstrip()
    # Drop trailing ">" quoted lines left above the marker.
    lines = candidate.splitlines()
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    candidate = "\n".join(lines).strip()

    if len(candidate) < 2 and len(text.strip()) > 2:
        return text.strip()
    return candidate


def parse_email(raw: bytes) -> ParsedEmail:
    """Turn raw RFC822 bytes into a :class:`ParsedEmail`."""
    message = message_from_bytes(raw)

    from_pairs = getaddresses(message.get_all("From", []))
    from_name, from_email = (from_pairs[0] if from_pairs else ("", ""))

    try:
        date = parsedate_to_datetime(message.get("Date")) if message.get("Date") else None
    except (TypeError, ValueError):
        date = None

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[ParsedAttachment] = []

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue

        content_type = (part.get_content_type() or "").lower()

        if _is_attachment(part):
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            content_id = (part.get("Content-ID") or "").strip() or None
            if content_id:
                content_id = content_id.strip("<>")
            disposition = (part.get_content_disposition() or "").lower()
            filename = _decode_header_value(part.get_filename()) or _default_name(content_type)
            attachments.append(
                ParsedAttachment(
                    filename=filename,
                    content_type=content_type,
                    data=payload,
                    content_id=content_id,
                    is_inline=disposition == "inline" or bool(content_id),
                )
            )
            continue

        if content_type == "text/plain":
            text_parts.append(_payload_text(part))
        elif content_type == "text/html":
            html_parts.append(_payload_text(part))

    html_body = "\n".join(p for p in html_parts if p.strip()) or None
    text_body = "\n".join(p for p in text_parts if p.strip())
    if not text_body and html_body:
        text_body = _html_to_text(html_body)

    return ParsedEmail(
        message_id=_normalise_message_id(message.get("Message-ID")),
        in_reply_to=_normalise_message_id(message.get("In-Reply-To")),
        references=_extract_references(message.get("References")),
        subject=_decode_header_value(message.get("Subject")) or "(no subject)",
        from_name=_decode_header_value(from_name),
        from_email=(from_email or "").lower().strip(),
        to_addresses=_addresses(message, "To"),
        cc_addresses=_addresses(message, "Cc"),
        delivered_to=_addresses(message, "Delivered-To")
        + _addresses(message, "X-Original-To")
        + _addresses(message, "X-Envelope-To"),
        date=date,
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
        is_auto_reply=detect_auto_reply(message),
    )


def _default_name(content_type: str) -> str:
    subtype = content_type.split("/")[-1] if "/" in content_type else "bin"
    return f"attachment.{re.sub(r'[^a-z0-9]+', '', subtype.lower())[:8] or 'bin'}"


def _html_to_text(html: str) -> str:
    """Rough HTML-to-text for when a mail has no text/plain alternative."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
