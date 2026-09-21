"""Password hashing and policy."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Argon2id with parameters comfortable for a homelab box: ~64 MiB, 3 passes.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify a password.

    A user with no hash (e.g. created from an inbound email) still burns the
    same work factor, so response timing does not reveal whether the account
    can log in at all.
    """
    if not password_hash:
        _hasher.hash(password)
        return False
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def password_problems(password: str, *, email: str = "") -> list[str]:
    """Return human-readable reasons a password is unacceptable."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        problems.append("must be 256 characters or fewer")
    classes = sum(
        bool(re.search(pattern, password))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 3 and len(password) < 20:
        problems.append(
            "needs upper case, lower case, digits and symbols (or be 20+ characters)"
        )
    local = email.split("@", 1)[0].lower()
    if local and len(local) > 2 and local in password.lower():
        problems.append("must not contain your email address")
    if password.lower() in {"password123!", "helpdesk1234", "changeme1234"}:
        problems.append("is a well-known password")
    return problems
