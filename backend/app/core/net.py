"""Who is calling: the caller's address, for throttling and the admin log."""

from __future__ import annotations

from starlette.requests import HTTPConnection


def client_ip(request: HTTPConnection) -> str:
    """Behind the reverse proxy the last `X-Forwarded-For` entry is the one the proxy added; anything before it was
    sent by the caller and can be forged."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]
