"""Attachment storage on disk.

Files are written under a generated path - never one derived from the
attacker-supplied filename - so a name like ``../../etc/cron.d/x`` cannot
escape the directory. The original name is kept in the database for display
and for the download header only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")

# Images we are willing to render inline in the GUI. Anything else downloads.
INLINE_SAFE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Magic-byte prefixes, checked because a declared Content-Type is just a claim.
_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


class AttachmentTooLarge(Exception):
    pass


def safe_filename(name: str, fallback: str = "attachment") -> str:
    """Flatten a filename to something harmless to echo back in a header."""
    name = unicodedata.normalize("NFKD", name or "")
    name = name.replace("\x00", "").strip()
    name = os.path.basename(name.replace("\\", "/"))
    name = _UNSAFE_NAME_RE.sub("_", name).strip(". ")
    if not name:
        name = fallback
    return name[:200]


def sniff_content_type(data: bytes, declared: str) -> str:
    """Trust magic bytes over the declared type for images."""
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in INLINE_SAFE_TYPES:
        # Claimed to be an image but is not one - do not let it render inline.
        return "application/octet-stream"
    return declared or "application/octet-stream"


def storage_root() -> Path:
    root = Path(settings.attachment_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_bytes(data: bytes, *, suffix: str = "") -> tuple[str, str, int]:
    """Write bytes to disk.

    Returns ``(relative_path, sha256_hex, size)``.
    """
    if len(data) > settings.max_attachment_bytes:
        raise AttachmentTooLarge(
            f"attachment is {len(data)} bytes, limit is {settings.max_attachment_bytes}"
        )

    digest = hashlib.sha256(data).hexdigest()
    now = datetime.now(UTC)
    suffix = _UNSAFE_NAME_RE.sub("", suffix)[:12]
    relative = f"{now:%Y/%m}/{digest[:2]}/{digest}{suffix}"

    target = storage_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Content-addressed, so an identical file is stored once.
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o640)
        tmp.replace(target)
    return relative, digest, len(data)


def resolve(relative_path: str) -> Path:
    """Map a stored path back to disk, refusing anything outside the root."""
    root = storage_root().resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("attachment path escapes the storage root")
    return candidate


def read(relative_path: str) -> bytes:
    return resolve(relative_path).read_bytes()


def delete(relative_path: str) -> None:
    try:
        resolve(relative_path).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:  # pragma: no cover - best effort
        log.warning("could not delete attachment %s: %s", relative_path, exc)
