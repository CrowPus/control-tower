"""One HTTP client for the social networks' APIs (tests give it a fake transport)."""

from __future__ import annotations

from typing import Any

import httpx

# Tests set this to an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class NetworkError(Exception):
    """A network's API refused or failed; the message is what the owner sees."""


def client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=transport, follow_redirects=True, headers={"User-Agent": "ControlTower/1.0"})


def explain(response: httpx.Response, network: str) -> str:
    """The network's own reason, short."""
    try:
        body: Any = response.json()
    except ValueError:
        return f"{network} answered {response.status_code}."
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return f"{network}: {error.get('message') or error.get('error_user_msg') or error}"[:400]
        if isinstance(error, str):
            return f"{network}: {body.get('error_description') or error}"[:400]
        if body.get("message"):
            return f"{network}: {body['message']}"[:400]
    return f"{network} answered {response.status_code}."


def check(response: httpx.Response, network: str) -> Any:
    if response.status_code >= 400:
        raise NetworkError(explain(response, network))
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}
