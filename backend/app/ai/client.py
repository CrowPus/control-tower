"""The AI client for structured answers (spec §11, §12, §76): the owner's provider (ai/providers.py) — Gemini,
Claude or OpenAI.

* one model-configuration service: reasoning / fast / embedding tiers from env
* typed structured output validated with Pydantic — invalid output is rejected
* timeout, bounded retries with exponential back-off + jitter, error classification
* per-call token/latency/cost tracking into `ai_usage` and budget enforcement
* result cache keyed by (operation, model, content hash) for identical inputs

When no API key is configured the `FakeProvider` answers deterministically so the
rest of the system (and the test-suite) works without credentials.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.errors import AIStructuredOutputInvalid, AIUnavailable, BudgetExceeded
from ..core.logging import get_logger, log_event
from ..db import transaction
from ..models import AICache, AIUsage

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

Tier = str  # "reasoning" | "fast"


@dataclass
class AICallResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cache_hit: bool = False


class ModelConfig:
    """The single place model names are resolved (spec §11)."""

    @staticmethod
    def resolve(tier: Tier) -> str:
        """The owner's provider's model for the tier (Admin → Platform → AI); embeddings are Gemini's."""
        from . import providers

        if tier == "embedding":
            return settings().gemini_embedding_model
        return providers.model("fast" if tier == "fast" else "reasoning")

    @staticmethod
    def describe() -> dict[str, Any]:
        from . import providers

        cfg = settings()
        return {
            **providers.describe(),
            "embedding_model": cfg.gemini_embedding_model,
            "daily_budget_usd": cfg.ai_daily_budget_usd,
            "monthly_budget_usd": cfg.ai_monthly_budget_usd,
        }


# --- usage / budget -------------------------------------------------------------------
def model_prices(model: str = "") -> tuple[float, float]:
    """(input, output) USD per million tokens for a model: its own list price, else the default."""
    from . import providers

    cfg = settings()
    name = (model or "").lower()
    table = providers.prices()
    for key in sorted(table, key=len, reverse=True):
        price = table[key]
        if name.startswith(key.lower()) and len(price) == 2:
            return float(price[0]), float(price[1])
    return cfg.ai_input_price_per_million_usd, cfg.ai_output_price_per_million_usd


def estimate_cost(input_tokens: int, output_tokens: int, model: str = "") -> float:
    price_in, price_out = model_prices(model)
    return round(input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out, 6)


def voice_cost(heard_seconds: float, spoken_seconds: float) -> float:
    """A voice conversation: the minutes of audio heard and spoken, each at its price (or every minute alike, older setting)."""
    cfg = settings()
    if cfg.ai_voice_price_per_minute_usd > 0:
        return round((heard_seconds + spoken_seconds) / 60 * cfg.ai_voice_price_per_minute_usd, 6)
    return round(heard_seconds / 60 * cfg.ai_voice_input_price_per_minute_usd + spoken_seconds / 60 * cfg.ai_voice_output_price_per_minute_usd, 6)


# All AI work in an install is the owner's: the agents, Nia, the picture and video studio. Each agent has its own
# budget (Admin → Team); these install-wide caps (AI_DAILY_BUDGET_USD, AI_MONTHLY_BUDGET_USD) are the last safety rail.
OWNER_AGENT_TYPE = "team"


def spend_since(since: datetime) -> float:
    """AI spend of the whole install since a moment."""
    with transaction() as session:
        total = session.execute(select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0)).where(AIUsage.created_at >= since)).scalar()
    return float(total or 0.0)


def check_budget() -> None:
    """Refuse a model call when the install's daily or monthly AI cap is used."""
    cfg = settings()
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if spend_since(day_start) >= cfg.ai_daily_budget_usd:
        raise BudgetExceeded("Today's AI cap for this install is used. It renews tomorrow (AI_DAILY_BUDGET_USD).")
    if spend_since(day_start.replace(day=1)) >= cfg.ai_monthly_budget_usd:
        raise BudgetExceeded("This month's AI cap for this install is used (AI_MONTHLY_BUDGET_USD).")


def record_usage(
    *,
    model: str,
    operation: str,
    agent_type: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    error_class: str = "",
    error_detail: str = "",
    cache_hit: bool = False,
    session: Session | None = None,
) -> None:
    row = AIUsage(
        agent_type=agent_type,
        model=model,
        operation=operation,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimate_cost(input_tokens, output_tokens, model) if not cache_hit else 0.0,
        latency_ms=latency_ms,
        success=success,
        error_class=error_class[:80],
        error_detail=error_detail[:400],
        cache_hit=cache_hit,
        created_at=datetime.now(UTC),
    )
    if session is not None:
        # Join the caller's transaction: SQLite allows one writer at a time, so a
        # separate write here would wait on the caller's own lock.
        session.add(row)
        return
    try:
        with transaction(write=True) as own:
            own.add(row)
    except Exception as exc:  # noqa: BLE001 - usage bookkeeping must never break a call
        log.warning("could not record AI usage: %s", exc.__class__.__name__)


# --- cache ------------------------------------------------------------------------------
def _cache_key(operation: str, model: str, prompt: str, schema_name: str) -> str:
    digest = hashlib.sha256(f"{operation}|{model}|{schema_name}|{prompt}".encode()).hexdigest()
    return f"{operation}:{digest[:40]}"


def cache_get(key: str) -> dict[str, Any] | None:
    with transaction() as session:
        row = session.execute(select(AICache).where(AICache.cache_key == key)).scalar_one_or_none()
        if row is None or row.expires_at < datetime.now(UTC):
            return None
        return dict(row.result)


def _cache_write(session: Session, key: str, operation: str, model: str, result: dict[str, Any], ttl_hours: int) -> None:
    now = datetime.now(UTC)
    existing = session.execute(select(AICache).where(AICache.cache_key == key)).scalar_one_or_none()
    if existing:
        existing.result = result
        existing.created_at = now
        existing.expires_at = now + timedelta(hours=ttl_hours)
    else:
        session.add(AICache(cache_key=key, operation=operation, model=model, result=result, created_at=now, expires_at=now + timedelta(hours=ttl_hours)))


def cache_put(key: str, operation: str, model: str, result: dict[str, Any], ttl_hours: int, session: Session | None = None) -> None:
    if session is not None:
        _cache_write(session, key, operation, model, result, ttl_hours)
        return
    try:
        with transaction(write=True) as own:
            _cache_write(own, key, operation, model, result, ttl_hours)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write AI cache: %s", exc.__class__.__name__)


# --- providers ------------------------------------------------------------------------------
class GeminiProvider:
    def close(self) -> None:
        self._client.close()

    def __init__(self, api_key: str) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        cfg = settings()
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(cfg.gemini_timeout_seconds * 1000)),
        )

    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None,
        temperature: float,
        max_output_tokens: int,
    ) -> AICallResult:
        types = self._types
        config_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = schema
        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        latency = int((time.perf_counter() - started) * 1000)
        usage = response.usage_metadata
        return AICallResult(
            text=response.text or "",
            model=model,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0)
            + int(getattr(usage, "thoughts_token_count", 0) or 0),
            latency_ms=latency,
        )


class FakeProvider:
    """Deterministic stand-in used when no API key is configured and in tests.

    It never invents candidate facts. Structured requests get the schema's
    defaults plus whatever `fake_hint` the caller passed (used by tests to
    simulate a model answer)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.canned: dict[str, dict[str, Any]] = {}

    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None,
        temperature: float,
        max_output_tokens: int,
    ) -> AICallResult:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema.__name__ if schema else None})
        if schema is None:
            return AICallResult(text="", model="fake", latency_ms=1)
        canned = self.canned.get(schema.__name__)
        if canned is not None:
            return AICallResult(text=json.dumps(canned), model="fake", latency_ms=1)
        try:
            instance = schema()  # all-defaults instance (schemas are designed for this)
        except ValidationError:
            # schemas with required fields: build a conservative "unknown" instance
            instance = _conservative_instance(schema)
        return AICallResult(text=instance.model_dump_json(), model="fake", latency_ms=1)


def _conservative_instance(schema: type[BaseModel]) -> BaseModel:
    values: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        if not info.is_required():
            continue
        annotation = info.annotation
        if annotation is int:
            values[name] = 0
        elif annotation is float:
            values[name] = 0.0
        elif annotation is bool:
            values[name] = False
        elif annotation is str:
            values[name] = ""
        else:
            # Literal / other: take the first allowed value if introspectable
            args = getattr(annotation, "__args__", ())
            values[name] = args[0] if args else None
    return schema(**values)


# --- service ----------------------------------------------------------------------------------
class GeminiService:
    def __init__(self, provider: Any | None = None) -> None:
        # A provider given here (tests) is always used; otherwise the owner's choice, looked up on every call.
        self._fixed = provider
        self._gemini: tuple[str, GeminiProvider] | None = None
        self._fake = FakeProvider()

    @property
    def provider(self) -> Any:
        from . import providers, vendors

        if self._fixed is not None:
            return self._fixed
        name = providers.provider()
        key = providers.key(name)
        if not key:
            return self._fake
        if name != "gemini":
            return vendors.VendorProvider(name)
        if self._gemini is None or self._gemini[0] != key:
            self._gemini = (key, GeminiProvider(key))
        return self._gemini[1]

    @property
    def is_live(self) -> bool:
        return not isinstance(self.provider, FakeProvider)

    @property
    def is_configured(self) -> bool:
        return self.is_live

    def structured(
        self,
        schema: type[T],
        *,
        operation: str,
        system: str,
        prompt: str,
        tier: Tier = "reasoning",
        agent_type: str = "",
        temperature: float = 0.2,
        cache_ttl_hours: int = 0,
        max_output_tokens: int = 8192,
        session: Session | None = None,
    ) -> tuple[T, AICallResult]:
        """Call the model and validate the answer against `schema`."""
        model = ModelConfig.resolve(tier)
        key = _cache_key(operation, model, f"{system}\n{prompt}", schema.__name__)
        if cache_ttl_hours > 0:
            cached = cache_get(key)
            if cached is not None:
                try:
                    parsed = schema.model_validate(cached)
                    record_usage(model=model, operation=operation, agent_type=agent_type, input_tokens=0, output_tokens=0, latency_ms=0, success=True, cache_hit=True, session=session)
                    return parsed, AICallResult(text="", model=model, cache_hit=True)
                except ValidationError:
                    pass
        if self.is_live:
            check_budget()
        result = self._call_with_retries(model=model, system=system, prompt=prompt, schema=schema, temperature=temperature, max_output_tokens=max_output_tokens, operation=operation, agent_type=agent_type, session=session)
        parsed = self._parse(schema, result.text, operation)
        if cache_ttl_hours > 0:
            cache_put(key, operation, model, parsed.model_dump(mode="json"), cache_ttl_hours, session=session)
        return parsed, result

    # --- internals ------------------------------------------------------------------------------
    def _call_with_retries(self, *, model: str, system: str, prompt: str, schema: type[BaseModel] | None, temperature: float, max_output_tokens: int, operation: str, agent_type: str, session: Session | None = None) -> AICallResult:
        cfg = settings()
        attempts = max(1, cfg.gemini_max_retries)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                result: AICallResult = self.provider.generate(model=model, system=system, prompt=prompt, schema=schema, temperature=temperature, max_output_tokens=max_output_tokens)
                record_usage(model=model, operation=operation, agent_type=agent_type, input_tokens=result.input_tokens, output_tokens=result.output_tokens, latency_ms=result.latency_ms, success=True, session=session)
                log_event(log, "ai call", operation=operation, model=model, input_tokens=result.input_tokens, output_tokens=result.output_tokens, latency_ms=result.latency_ms, attempt=attempt)
                return result
            except Exception as exc:  # noqa: BLE001 - classified below
                latency = int((time.perf_counter() - started) * 1000)
                retryable, classification = _classify(exc)
                record_usage(model=model, operation=operation, agent_type=agent_type, input_tokens=0, output_tokens=0, latency_ms=latency, success=False, error_class=classification, error_detail=error_detail(exc), session=session)
                last_error = exc
                log_event(log, "ai call failed", level=30, operation=operation, model=model, attempt=attempt, error_class=classification, retryable=retryable)
                if not retryable or attempt == attempts:
                    break
                time.sleep(min(30.0, (2 ** (attempt - 1)) + random.uniform(0, 1)))  # noqa: S311
        raise AIUnavailable(f"AI call failed ({_classify(last_error)[1] if last_error else 'unknown'}).") from last_error

    @staticmethod
    def _parse(schema: type[T], text: str, operation: str) -> T:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            log_event(log, "ai output invalid", level=30, operation=operation, schema=schema.__name__, errors=str(exc)[:500])
            raise AIStructuredOutputInvalid(f"Model output for {operation} did not match {schema.__name__}.") from exc


_SECRET_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z_\-]{20,}|(?i:(api[_-]?key|key|token)=)[^&\s'\"]+")


def error_detail(exc: Exception) -> str:
    """What the provider said, short and without anything that looks like a key.
    The request itself (prompt, candidate data) is never part of it."""
    text = " ".join(str(exc).split())
    return _SECRET_RE.sub("[redacted]", text)[:400]


def _classify(exc: Exception | None) -> tuple[bool, str]:
    if exc is None:
        return False, "unknown"
    if isinstance(exc, ValueError | TypeError):
        return False, "schema_or_request_error"
    name = exc.__class__.__name__
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status in (429,) or "resource_exhausted" in message or "rate" in message and "limit" in message:
        return True, "rate_limited"
    if status in (500, 502, 503, 504) or "unavailable" in message or "deadline" in message or "timeout" in message or "timed out" in message:
        return True, "transient"
    if status in (400, 401, 403, 404):
        return False, f"client_error_{status}"
    if "ServerError" in name:
        return True, "server_error"
    if "ClientError" in name:
        return False, "client_error"
    return True, name.lower()


_service: GeminiService | None = None


def gemini() -> GeminiService:
    global _service
    if _service is None:
        _service = GeminiService()
    return _service


def reset_for_tests(provider: Any | None = None) -> GeminiService:
    global _service
    _service = GeminiService(provider=provider or FakeProvider())
    return _service
