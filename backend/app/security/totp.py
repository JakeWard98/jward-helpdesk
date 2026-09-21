"""TOTP enrolment and verification (RFC 6238, 30s step, SHA-1, 6 digits)."""

from __future__ import annotations

import io
import time

import pyotp
import segno

from app.security.crypto import new_token, sha256_hex

DIGITS = 6
PERIOD = 30
# One step either side absorbs clock drift without widening the window much.
VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str, issuer: str = "jward-helpdesk") -> str:
    return pyotp.TOTP(secret, digits=DIGITS, interval=PERIOD).provisioning_uri(
        name=account, issuer_name=issuer
    )


def qr_svg(uri: str) -> str:
    """Render the enrolment URI as an inline SVG.

    Generated server-side so the secret never has to travel to a third-party
    QR service, and the frontend needs no QR dependency.
    """
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111827")
    return buf.getvalue().decode()


def current_counter(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // PERIOD)


def verify_code(secret: str, code: str, *, last_counter: int | None = None) -> int | None:
    """Verify a TOTP code and return the counter it matched, or ``None``.

    Returning the counter lets the caller store it and refuse the same code a
    second time, so an intercepted code cannot be replayed inside its window.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None

    totp = pyotp.TOTP(secret, digits=DIGITS, interval=PERIOD)
    now = current_counter()
    for offset in range(-VALID_WINDOW, VALID_WINDOW + 1):
        counter = now + offset
        if totp.verify(code, for_time=counter * PERIOD, valid_window=0):
            if last_counter is not None and counter <= last_counter:
                return None  # already used
            return counter
    return None


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Readable one-time codes, shown once at enrolment."""
    codes = []
    for _ in range(count):
        raw = new_token(8).replace("-", "").replace("_", "")[:16].lower()
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return sha256_hex(normalise_recovery_code(code))


def normalise_recovery_code(code: str) -> str:
    return (code or "").strip().lower().replace(" ", "")
