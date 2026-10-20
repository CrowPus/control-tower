"""What went wrong with an AI provider call, safe to log and safe to show.

Provider exceptions can carry request URLs (with the key) and raw provider text,
so neither is logged nor shown: the log gets the error's kind (class, HTTP code,
status such as RESOURCE_EXHAUSTED) and the member a neutral sentence that never
names the provider or asks them to check an integration they cannot see.
"""

from __future__ import annotations

import logging
from typing import Any

from google.genai import errors as genai_errors

from ..core.logging import get_logger, log_event
from .providers import ProviderError

log = get_logger("app.ai")

# Brief provider trouble, worth one more try.
TRANSIENT_CODES = frozenset({500, 502, 503, 504})

CHAT_BUSY = "Nia is busy right now. Please try again in a minute."
CHAT_FAILED = "Nia couldn't answer just now. Please try again in a moment."
VOICE_FAILED = "Voice is unavailable right now. You can continue with text."


def kind(exc: BaseException) -> dict[str, Any]:
    """The error's class and, for provider API errors, its HTTP code and status. Never its message."""
    out: dict[str, Any] = {"error_class": type(exc).__name__}
    if isinstance(exc, genai_errors.APIError):
        out["code"] = exc.code
        out["status"] = exc.status
    elif isinstance(exc, ProviderError):
        out["code"] = exc.code
    return out


def transient(exc: BaseException) -> bool:
    return isinstance(exc, (genai_errors.APIError, ProviderError)) and exc.code in TRANSIENT_CODES


def busy(exc: BaseException) -> bool:
    return isinstance(exc, (genai_errors.APIError, ProviderError)) and exc.code == 429


def record(what: str, exc: BaseException, **fields: Any) -> None:
    log_event(log, what, level=logging.ERROR, **kind(exc), **fields)
