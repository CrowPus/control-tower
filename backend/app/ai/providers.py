"""Which AI the install thinks with, chosen by the owner (Admin → Platform → AI), and the keys for it.

Text work — the agents, Nia's chat, meetings, the brain, every structured answer — goes to the owner's provider:
Google Gemini, Anthropic Claude, or OpenAI (or any service that speaks OpenAI's API, at the owner's address). Pictures,
videos, Nia's voice, Nia's browser and web search are Gemini's own and use the Gemini key whichever provider thinks.

The owner's choice is the platform setting "ai"; keys are stored encrypted. A key set in the environment
(GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY) is used when the admin has none. Read again at most every
REFRESH_SECONDS in each process.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..core import crypto

Provider = Literal["gemini", "anthropic", "openai"]
PROVIDERS: tuple[Provider, ...] = ("gemini", "anthropic", "openai")
LABELS = {"gemini": "Google Gemini", "anthropic": "Anthropic Claude", "openai": "OpenAI (or a compatible service)"}
SETTING = "ai"
REFRESH_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"value": None, "read_at": 0.0}


class ProviderError(Exception):
    """A provider answered with an error. `status` is its HTTP status; the message never holds the key."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message[:300]}")
        self.status = status
        self.code = status


def stored() -> dict[str, Any]:
    """The owner's AI settings as saved (keys encrypted)."""
    if time.monotonic() - _cache["read_at"] > REFRESH_SECONDS or _cache["value"] is None:
        from ..db import transaction
        from ..services import platform

        try:
            with transaction() as session:
                value = dict(platform.get(session, SETTING) or {})
        except SQLAlchemyError:
            value = {}
        with _lock:
            _cache.update(value=value, read_at=time.monotonic())
    return dict(_cache["value"] or {})


def remember(value: dict[str, Any]) -> None:
    with _lock:
        _cache.update(value=dict(value), read_at=time.monotonic())


def provider() -> Provider:
    chosen = stored().get("provider")
    return chosen if chosen in PROVIDERS else "gemini"


def _env_key(name: Provider) -> str:
    cfg = settings()
    return {"gemini": cfg.gemini_api_key, "anthropic": cfg.anthropic_api_key, "openai": cfg.openai_api_key}[name]


def key(name: Provider) -> str:
    enc = (stored().get("keys") or {}).get(name)
    if enc:
        return crypto.decrypt(enc, associated_data=f"ai-key:{name}")
    return _env_key(name)


def key_source(name: Provider) -> Literal["admin", "environment", "none"]:
    if (stored().get("keys") or {}).get(name):
        return "admin"
    return "environment" if _env_key(name) else "none"


def gemini_key() -> str:
    """The key for Gemini's own work (pictures, videos, voice, browser, web search)."""
    return key("gemini")


def configured() -> bool:
    """Whether the chosen provider can think (a key, and model names)."""
    name = provider()
    return bool(key(name)) and bool(model("reasoning")) and bool(model("fast"))


def openai_base_url() -> str:
    return (stored().get("openai_base_url") or settings().openai_base_url or "https://api.openai.com/v1").rstrip("/")


def default_models(name: Provider) -> dict[str, str]:
    cfg = settings()
    if name == "gemini":
        return {"reasoning": cfg.gemini_reasoning_model, "fast": cfg.gemini_fast_model or cfg.gemini_reasoning_model}
    if name == "anthropic":
        return {"reasoning": cfg.anthropic_reasoning_model, "fast": cfg.anthropic_fast_model}
    return {"reasoning": cfg.openai_reasoning_model, "fast": cfg.openai_fast_model}


def model(tier: str, name: Provider | None = None) -> str:
    """The model for a tier ("reasoning" or "fast") of a provider (default: the chosen one)."""
    name = name or provider()
    mine = ((stored().get("models") or {}).get(name) or {}).get("fast" if tier == "fast" else "reasoning")
    return str(mine or default_models(name)["fast" if tier == "fast" else "reasoning"] or "")


def prices() -> dict[str, list[float]]:
    """List prices per model, USD per million tokens [input, output]: the owner's over the environment's."""
    return {**settings().ai_model_prices, **(stored().get("prices") or {})}


def describe() -> dict[str, Any]:
    name = provider()
    return {"provider": name, "configured": configured(), "reasoning_model": model("reasoning"), "fast_model": model("fast"),
            "gemini_key": bool(gemini_key())}
