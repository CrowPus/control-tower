"""Authenticator-app codes (RFC 6238 TOTP: HMAC-SHA1, 6 digits, 30-second steps).

The standard every authenticator app speaks. A code is accepted for the current
step and one step either side (clock drift), and never twice: the caller stores
the last accepted step and passes it back.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return str(number).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def code(secret: str, now: float | None = None) -> str:
    return _code_at(secret, current_step(now))


def verify(secret: str, candidate: str, *, last_step: int = 0, now: float | None = None) -> int | None:
    """The step the code belongs to, or None. A step at or before `last_step` was
    already used and is refused (no replay)."""
    candidate = "".join(ch for ch in candidate if ch.isdigit())
    if len(candidate) != DIGITS:
        return None
    step = current_step(now)
    for offset in (0, -1, 1):
        at = step + offset
        if at <= last_step:
            continue
        if hmac.compare_digest(_code_at(secret, at), candidate):
            return at
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
