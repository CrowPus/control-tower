"""Admin → Platform → AI: which provider the install thinks with, its keys, models and prices, and a test call.
And the first-run setup: what is still missing before the team can work, checked step by step."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..ai import providers
from ..ai.client import ModelConfig, record_usage
from ..core import crypto
from ..core.errors import ValidationFailed
from ..models import OwnerAgent
from ..services import platform, system_mail
from ..services.marketing import brand
from ..services.plugins import core as plugins
from ..services.team import config as team_config
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api", tags=["admin-ai"])

ProviderName = Literal["gemini", "anthropic", "openai"]
# What only Gemini does, whichever provider thinks (each needs the Gemini key).
GEMINI_ONLY = ["Pictures and videos (Marketing → Studio)", "Nia's voice", "Nia's browser", "Web search for agents"]


class ProviderOut(BaseModel):
    key: ProviderName
    label: str
    key_source: Literal["admin", "environment", "none"]
    reasoning_model: str
    fast_model: str
    default_reasoning_model: str
    default_fast_model: str


class AIOut(BaseModel):
    provider: ProviderName
    configured: bool
    providers: list[ProviderOut]
    prices: dict[str, list[float]]
    openai_base_url: str
    gemini_only: list[str]
    gemini_key: bool


class ModelsIn(BaseModel):
    reasoning: str = Field(default="", max_length=100)
    fast: str = Field(default="", max_length=100)


class AIIn(BaseModel):
    provider: ProviderName
    # A new key per provider; empty or left out: keep the one set; null: remove the admin's (the environment's stays).
    keys: dict[ProviderName, str | None] = Field(default_factory=dict)
    models: dict[ProviderName, ModelsIn] = Field(default_factory=dict)
    # USD per million tokens [input, output] per model name.
    prices: dict[str, list[float]] = Field(default_factory=dict)
    openai_base_url: str = Field(default="", max_length=300)


class AITestOut(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int
    message: str


def _out() -> AIOut:
    stored = providers.stored()
    return AIOut(
        provider=providers.provider(), configured=providers.configured(),
        providers=[ProviderOut(key=p, label=providers.LABELS[p], key_source=providers.key_source(p), reasoning_model=providers.model("reasoning", p),
                               fast_model=providers.model("fast", p), default_reasoning_model=providers.default_models(p)["reasoning"],
                               default_fast_model=providers.default_models(p)["fast"]) for p in providers.PROVIDERS],
        prices={k: list(v) for k, v in providers.prices().items()}, openai_base_url=str(stored.get("openai_base_url") or ""),
        gemini_only=GEMINI_ONLY, gemini_key=bool(providers.gemini_key()),
    )


@router.get("/ai", response_model=AIOut)
def get_ai(_ctx: CurrentAdmin) -> AIOut:
    return _out()


@router.put("/ai", response_model=AIOut)
def put_ai(data: AIIn, ctx: OwnerAdmin, session: AdminDb) -> AIOut:
    current = dict(platform.get(session, providers.SETTING) or {})
    keys = dict(current.get("keys") or {})
    for name, value in data.keys.items():
        if value is None:
            keys.pop(name, None)
        elif value.strip():
            keys[name] = crypto.encrypt(value.strip(), associated_data=f"ai-key:{name}")
    models = {k: dict(v) for k, v in (current.get("models") or {}).items()}
    for name, chosen in data.models.items():
        models[name] = {"reasoning": chosen.reasoning.strip(), "fast": chosen.fast.strip()}
    prices = {k.strip(): [float(v[0]), float(v[1])] for k, v in data.prices.items() if k.strip() and len(v) == 2 and v[0] >= 0 and v[1] >= 0}
    url = data.openai_base_url.strip().rstrip("/")
    if url and not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValidationFailed("The OpenAI-compatible address starts with https:// (or http://localhost).", code="INVALID_AI_SETTINGS")
    new = {**current, "provider": data.provider, "keys": keys, "models": models, "prices": prices, "openai_base_url": url}
    previous = providers.stored()
    providers.remember(new)
    try:
        problems = []
        if not providers.key(data.provider):
            problems.append(f"a {providers.LABELS[data.provider]} key")
        for tier in ("reasoning", "fast"):
            model = providers.model(tier, data.provider)
            if not model:
                problems.append(f"the {tier} model's name")
            elif data.provider != "gemini" and not any(model.lower().startswith(k.lower()) for k in providers.prices()):
                problems.append(f"the price of {model} (so budgets count right)")
        if problems:
            raise ValidationFailed(f"Before {providers.LABELS[data.provider]} can think, set: {', '.join(problems)}.", code="INVALID_AI_SETTINGS")
    except ValidationFailed:
        providers.remember(previous)
        raise
    platform.put(session, providers.SETTING, new)
    record(session, ctx.admin, "ai.changed", target_type="platform", target_id="ai", ip=ctx.ip,
           metadata={"provider": data.provider, "keys_changed": sorted(k for k, v in data.keys.items() if v is None or (v or "").strip()),
                     "models": models, "prices": prices})
    return _out()


@router.post("/ai/test", response_model=AITestOut)
def test_ai(ctx: OwnerAdmin, session: AdminDb) -> AITestOut:
    """One short answer from the chosen provider's fast model: the key, the model name and the address work."""
    from ..ai.client import gemini

    service = gemini()
    name, model = providers.provider(), ModelConfig.resolve("fast")
    if not service.is_live:
        return AITestOut(ok=False, provider=name, model=model, latency_ms=0, message=f"No {providers.LABELS[name]} key is set.")
    started = time.perf_counter()
    try:
        result = service.provider.generate(model=model, system="Answer with the single word OK.", prompt="Are you there?", schema=None,
                                           temperature=0.0, max_output_tokens=20)
    except Exception as exc:  # noqa: BLE001 - the provider's answer is the test's result
        from ..ai.client import error_detail

        record(session, ctx.admin, "ai.tested", target_type="platform", target_id="ai", ip=ctx.ip, result="failed", metadata={"provider": name})
        return AITestOut(ok=False, provider=name, model=model, latency_ms=int((time.perf_counter() - started) * 1000), message=error_detail(exc))
    record_usage(model=model, operation="admin.ai_test", agent_type="team", input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                 latency_ms=result.latency_ms, success=True, session=session)
    record(session, ctx.admin, "ai.tested", target_type="platform", target_id="ai", ip=ctx.ip, metadata={"provider": name, "model": model})
    return AITestOut(ok=True, provider=name, model=model, latency_ms=result.latency_ms, message=(result.text or "").strip()[:100] or "(an empty answer)")


# --- the first-run setup -------------------------------------------------------------------------------------------
class SetupStep(BaseModel):
    key: str
    title: str
    done: bool
    required: bool
    detail: str


class SetupOut(BaseModel):
    finished: bool
    steps: list[SetupStep]


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    about: str = Field(min_length=20, max_length=2000)


def _setup(session: AdminDb) -> SetupOut:
    kit = brand.kit(session)
    team = team_config.team(session)
    agents = session.execute(select(func.count()).select_from(OwnerAgent).where(OwnerAgent.status == "active")).scalar() or 0
    plugins.ensure()
    on = [k for k in plugins.LOADED if plugins.enabled(k)]
    steps = [
        SetupStep(key="company", title="Your company", done=bool(kit.about.strip()), required=True,
                  detail="Its name and, in your words, what it does and for whom. Every agent reads this first."),
        SetupStep(key="ai", title="The AI your team thinks with", done=providers.configured(), required=True,
                  detail="Gemini, Claude or OpenAI, with your own key."),
        SetupStep(key="team", title="The team's switch and budget", done=bool(team.enabled and team.language and team.ai_daily_usd),
                  required=True, detail="Switched on, the language agents write to you in, and a daily AI budget."),
        SetupStep(key="mail", title="E-mail for the install", done=system_mail.is_configured(), required=False,
                  detail="SMTP in the server's .env: invitations for other admins, alerts and the Monday report. Without it, the bell in the admin."),
        SetupStep(key="agent", title="Your first agent", done=agents > 0, required=False, detail="Team → New agent: blank or from a starter template."),
        SetupStep(key="project", title="Your project connected", done=bool(on), required=False, detail="Plugins: a Python plugin or a project over HTTP."),
    ]
    finished = bool((platform.get(session, "setup") or {}).get("finished"))
    return SetupOut(finished=finished, steps=steps)


@router.get("/setup", response_model=SetupOut)
def get_setup(_ctx: CurrentAdmin, session: AdminDb) -> SetupOut:
    return _setup(session)


@router.put("/setup/company", response_model=SetupOut)
def put_company(data: CompanyIn, ctx: OwnerAdmin, session: AdminDb) -> SetupOut:
    kit = brand.kit(session)
    brand.save_kit(session, kit.model_copy(update={"name": data.name.strip(), "about": data.about.strip()}))
    record(session, ctx.admin, "setup.company", target_type="platform", target_id="setup", ip=ctx.ip)
    return _setup(session)


@router.post("/setup/finish", response_model=SetupOut)
def finish_setup(ctx: OwnerAdmin, session: AdminDb) -> SetupOut:
    out = _setup(session)
    missing = [s.title for s in out.steps if s.required and not s.done]
    if missing:
        raise ValidationFailed(f"Still to do: {', '.join(missing)}.", code="SETUP_INCOMPLETE")
    platform.put(session, "setup", {"finished": True})
    record(session, ctx.admin, "setup.finished", target_type="platform", target_id="setup", ip=ctx.ip)
    return _setup(session)
