"""The owner's settings for the team (platform setting `agents`, Admin → Team).

Nothing has a business value in code: an agent is off, has no model tier, no budget, no
schedule and no tools until the owner sets them. An agent that is switched on but misses a
setting does not run; it reports what it is waiting for (`status`).

Shape: {"team": TeamSettings, "by_agent": {key: AgentSettings}}.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .. import platform
from . import registry

Tier = Literal["fast", "reasoning"]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TeamSettings(BaseModel):
    # The team's master switch: off stops every agent at once.
    enabled: bool = False
    # The language the agents write to the owner in (reports, summaries, proposals).
    language: Literal["en", "de"] | None = None
    ai_daily_usd: float | None = Field(default=None, ge=0)
    ai_monthly_usd: float | None = Field(default=None, ge=0)
    # Earned autonomy (A-D2): after this many approvals in a row without an edit, the admin
    # app offers to let that action type run alone. Empty: never offered.
    autonomy_offer_after: int | None = Field(default=None, ge=1)


class AgentSchedule(BaseModel):
    # Times are in the owner's time zone (TIME_ZONE). Empty: the agent runs on its events and when started by hand.
    every_minutes: int | None = Field(default=None, ge=1)
    times: list[str] = Field(default_factory=list)
    # 0 = Monday. The days the times and "every … minutes" apply on; empty = every day.
    weekdays: list[int] = Field(default_factory=list)

    @field_validator("times")
    @classmethod
    def _times(cls, value: list[str]) -> list[str]:
        for t in value:
            if not _HHMM.match(t):
                raise ValueError(f"Time {t!r} must be HH:MM")
        return sorted(set(value))

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in value):
            raise ValueError("Weekdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))


class AgentSettings(BaseModel):
    enabled: bool = False
    tier: Tier | None = None
    ai_daily_usd: float | None = Field(default=None, ge=0)
    schedule: AgentSchedule = Field(default_factory=AgentSchedule)
    tools: list[str] = Field(default_factory=list)
    # Per action type: "L1" (each one approved) or "L2" (alone within l2_daily_limit).
    levels: dict[str, Literal["L1", "L2"]] = Field(default_factory=dict)
    l2_daily_limit: dict[str, int] = Field(default_factory=dict)
    # Most proposals of a type per day; a type without a number has no cap (the owner approves each).
    proposals_per_day: dict[str, int] = Field(default_factory=dict)
    # Paused and reported when a result costs more than this (empty: not checked).
    max_cost_per_result_usd: float | None = Field(default=None, ge=0)


def _raw(session: Session) -> dict[str, Any]:
    value = platform.get(session, "agents") or {}
    return value if isinstance(value, dict) else {}


def team(session: Session) -> TeamSettings:
    return TeamSettings.model_validate(_raw(session).get("team") or {})


def agent(session: Session, key: str) -> AgentSettings:
    return AgentSettings.model_validate((_raw(session).get("by_agent") or {}).get(key) or {})


def check(key: str, value: AgentSettings) -> AgentSettings:
    """Refuse settings the agent's design does not allow (ValueError with the reason)."""
    spec = registry.get(key)
    if spec is None:
        raise ValueError(f"Unknown agent: {key}")
    unknown_tools = set(value.tools) - set(registry.tools_of(spec))
    if unknown_tools:
        raise ValueError(f"{spec.name} cannot use: {', '.join(sorted(unknown_tools))}")
    allowed = set(registry.actions_of(spec))
    for field_name in ("levels", "l2_daily_limit", "proposals_per_day"):
        unknown = set(getattr(value, field_name)) - allowed
        if unknown:
            raise ValueError(f"{spec.name} has no action: {', '.join(sorted(unknown))}")
    for action, level in value.levels.items():
        if level == "L2" and registry.ACTIONS[action].ceiling == "L3":
            raise ValueError(f"{registry.ACTIONS[action].label} is always decided by the owner.")
        if level == "L2" and value.l2_daily_limit.get(action) is None:
            raise ValueError(f"Set how many '{registry.ACTIONS[action].label}' may run alone per day before allowing it.")
    if any(n < 0 for n in [*value.l2_daily_limit.values(), *value.proposals_per_day.values()]):
        raise ValueError("Limits cannot be negative.")
    if value.schedule.weekdays and not value.schedule.times and not value.schedule.every_minutes:
        raise ValueError("Ticked days alone never start the agent: add a time or 'every … minutes' for those days, or untick the days.")
    return value


def schedule_text(value: AgentSchedule) -> str:
    """The schedule in one sentence, as the owner sees it on the Team page."""
    if not value.times and not value.every_minutes:
        return "No schedule: runs only when events arrive for it or you start it."
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    parts = []
    if value.every_minutes:
        parts.append(f"every {value.every_minutes} min")
    if value.times:
        parts.append("at " + ", ".join(value.times))
    days = ", ".join(names[d] for d in value.weekdays) if value.weekdays else "every day"
    return f"{' and '.join(parts).capitalize()}, {days} (your time zone)."


def save_agent(session: Session, key: str, value: AgentSettings) -> None:
    raw = _raw(session)
    by_agent = dict(raw.get("by_agent") or {})
    by_agent[key] = check(key, value).model_dump()
    platform.put(session, "agents", {**raw, "by_agent": by_agent})


def save_team(session: Session, value: TeamSettings) -> None:
    raw = _raw(session)
    platform.put(session, "agents", {**raw, "team": value.model_dump()})


def level_of(settings_: AgentSettings, action: str) -> Literal["L1", "L2", "L3"]:
    if registry.ACTIONS[action].ceiling == "L3":
        return "L3"
    return settings_.levels.get(action, "L1")


Status = Literal["unavailable", "off", "waiting", "ready"]


def status(session: Session, key: str) -> tuple[Status, list[str]]:
    """Whether the agent can run now, and if it waits, for which settings."""
    spec = registry.get(key)
    if spec is None:
        return "unavailable", []
    mine = agent(session, key)
    whole = team(session)
    if not mine.enabled:
        return "off", []
    waiting: list[str] = []
    if not whole.enabled:
        waiting.append("Team switched on")
    if whole.language is None:
        waiting.append("Team language")
    if whole.ai_daily_usd is None:
        waiting.append("Team AI budget per day")
    if mine.tier is None:
        waiting.append("Model tier")
    if mine.ai_daily_usd is None:
        waiting.append("AI budget per day")
    if not mine.tools:
        waiting.append("Tools")
    return ("waiting", waiting) if waiting else ("ready", [])
