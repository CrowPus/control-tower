"""Signatures between Control Tower and a project, both ways (docs/PLUGINS.md → Signing).

Header `X-Control-Tower-Signature: t=<unix seconds>,v1=<hex>`, where hex = HMAC-SHA256(secret, "<t>.<path>." + body).
The path (without host or query) is signed, so a signature for one address is worthless for another; a signature
older or newer than TOLERANCE seconds is refused, so a recorded request cannot be replayed later.
"""

from __future__ import annotations

import hashlib
import hmac
import time

HEADER = "X-Control-Tower-Signature"
# How far apart the two clocks may be, and how long a signed request stays valid.
TOLERANCE = 300


def sign(secret: str, path: str, body: bytes, *, now: int | None = None) -> str:
    t = int(now if now is not None else time.time())
    mac = hmac.new(secret.encode(), f"{t}.{path}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, path: str, body: bytes, header: str, *, now: int | None = None) -> bool:
    if not secret or not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if abs(current - t) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{t}.{path}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)
