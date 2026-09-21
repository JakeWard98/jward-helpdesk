"""Key derivation and symmetric encryption for secrets that must be reversible.

Only TOTP seeds fall into that category. Everything else (passwords, session
tokens, recovery codes) is hashed one-way and never decrypted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# Distinct info strings keep the keys for different purposes independent: a
# leak of one does not weaken the others.
_TOTP_INFO = b"helpdesk.totp.v1"
_MAIL_INFO = b"helpdesk.mailtoken.v1"


def _derive(info: bytes, length: int = 32) -> bytes:
    """HKDF-style expansion of APP_SECRET into a purpose-specific key."""
    prk = hmac.new(b"jward-helpdesk", settings.app_secret.encode(), hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(_TOTP_INFO)))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:  # APP_SECRET changed, or the row was tampered with
        raise ValueError("stored secret could not be decrypted") from exc


def mail_token_key() -> bytes:
    return _derive(_MAIL_INFO)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """URL-safe random token. 32 bytes = 256 bits of entropy."""
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
