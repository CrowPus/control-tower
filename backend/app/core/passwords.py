"""Password hashing and opaque token helpers (spec §48).

Argon2id via argon2-cffi. Session tokens are 256-bit random values; only their
SHA-256 digest is stored, so a database read never yields a usable session.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def password_policy_errors(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < 12:
        errors.append("Use at least 12 characters.")
    if len(password) > 256:
        errors.append("Password is too long.")
    if password.strip() != password:
        errors.append("Password must not start or end with whitespace.")
    if password.lower() in {"password1234", "123456789012", "qwertyuiop12"}:
        errors.append("Password is too common.")
    return errors
