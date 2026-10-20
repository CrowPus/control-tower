"""The owner's own agents (docs/PLAN.md §4–5): made from the agent template — blank or a starter template — changed and
archived by the owner, or proposed by Nia and made when the owner clears it.

The template is checked against the rules every time it is saved: an agent that breaks one is never saved. The rules
that hold while it works (it only proposes; money, deleting, settings and agents' instructions are always the owner's;
budgets; outside text is information; no duplicates; everything logged; a stop switch; a report every run) are the
engine's and cannot be switched off by any template.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import OwnerAgent, TeamApproval
from . import approvals, config, registry
from . import templates as starter

# Nia's own tools: no other agent gets them, also not with clearance (they decide clearance, change the admin, give
# directives, start or question other agents, drive the browser, or read Nia's memory of the owner).
NIA_ONLY_TOOLS = frozenset({
    "decide_approvals", "confirm_directive", "propose_directive", "propose_admin_change", "admin_endpoints", "read_settings",
    "run_agent", "ask_agent", "agent_templates", "browse", "browser_status", "browser_answer", "remember", "read_memories", "add_slide",
})
# Actions no agent of the owner may propose: changing the admin, and making agents.
NIA_ONLY_ACTIONS = frozenset({"admin.change", "agent.create"})
# Safety rails, not business values.
MIN_JOB = 20
MAX_EVENTS = 10
MAX_ROUNDS = 30
_EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_KEY = re.compile(r"[^a-z0-9]+")


class Refused(ValueError):
    """The template breaks a rule; the message says which."""


class Design(BaseModel):
    """The agent template: everything that makes an agent who it is."""

    name: str = Field(min_length=2, max_length=60)
    department: str = Field(min_length=2, max_length=40)
    job: str = Field(max_length=2000)
    guide: str = Field(default="", max_length=6000)
    tools: list[str] = Field(default_factory=list, max_length=40)
    actions: list[str] = Field(default_factory=list, max_length=20)
    subscribes: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    kpi: str = Field(default="", max_length=60)
    max_rounds: int = Field(default=0, ge=0)

    @field_validator("name", "department", "job", "guide", "kpi")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def offered_tools() -> list[str]:
    """Every tool the template offers: the catalogue without the common ones (every agent has those) and Nia's own."""
    from . import tools

    return sorted(t for t in tools.CATALOGUE if t not in NIA_ONLY_TOOLS and t not in registry.COMMON_TOOLS and t not in tools.ALWAYS)


def offered_actions() -> list[str]:
    return sorted(a for a in registry.ACTIONS if a not in NIA_ONLY_ACTIONS and a not in registry.COMMON_ACTIONS)


def check(design: Design, *, key: str = "") -> list[str]:
    """Every rule the template breaks (empty: it may be saved)."""
    from ..insights import queries

    problems: list[str] = []
    if len(design.job) < MIN_JOB:
        problems.append(f"Write the agent's job in a sentence or two (at least {MIN_JOB} characters).")
    tools = [t for t in design.tools if t not in registry.COMMON_TOOLS]
    if not tools:
        problems.append("Give the agent at least one tool besides the ones every agent has.")
    nia = sorted(set(design.tools) & NIA_ONLY_TOOLS)
    if nia:
        problems.append(f"Only Nia uses: {', '.join(nia)}.")
    unknown = sorted(set(design.tools) - set(offered_tools()) - set(registry.COMMON_TOOLS) - NIA_ONLY_TOOLS)
    if unknown:
        problems.append(f"No such tool: {', '.join(unknown)}.")
    forbidden = sorted(set(design.actions) & NIA_ONLY_ACTIONS)
    if forbidden:
        problems.append(f"No agent of yours may propose: {', '.join(forbidden)}.")
    unknown = sorted(set(design.actions) - set(registry.ACTIONS))
    if unknown:
        problems.append(f"No such action: {', '.join(unknown)}.")
    if design.actions and "propose" not in [*design.tools, *registry.COMMON_TOOLS]:
        problems.append("An agent that proposes actions needs the propose tool.")
    for field_name in ("subscribes", "emits"):
        events = getattr(design, field_name)
        if len(events) > MAX_EVENTS:
            problems.append(f"At most {MAX_EVENTS} events in {field_name}.")
        bad = [e for e in events if not _EVENT.match(e)]
        if bad:
            problems.append(f"Event names look like 'marketing.brief': {', '.join(bad)}.")
    if design.kpi and design.kpi not in queries.BY_KEY:
        problems.append(f"The goal number is one of the Insights numbers: {', '.join(queries.BY_KEY)}.")
    if design.max_rounds > MAX_ROUNDS:
        problems.append(f"At most {MAX_ROUNDS} rounds of tool use per run.")
    if key and (key in registry.BUILT_IN or key.startswith("ext_")):
        problems.append("That agent is built in and cannot be changed here.")
    return problems


def make_key(session: Session, name: str) -> str:
    base = _KEY.sub("_", name.lower()).strip("_")[:30] or "agent"
    if base in registry.BUILT_IN or base.startswith("ext"):
        base = f"my_{base}"[:30]
    key, n = base, 2
    while session.execute(select(OwnerAgent.id).where(OwnerAgent.key == key)).first() is not None:
        key, n = f"{base[:27]}_{n}", n + 1
    return key


def _spec(key: str, design: Design) -> registry.Agent:
    return registry.Agent(key=key, name=design.name, department=design.department, purpose=design.job, kpi=design.kpi,
                          subscribes=tuple(design.subscribes), emits=tuple(design.emits), tools=tuple(dict.fromkeys(design.tools)),
                          actions=tuple(dict.fromkeys(design.actions)), guide=design.guide, max_rounds=design.max_rounds)


def from_template(key: str) -> tuple[Design, str]:
    """A starter template as an editable design, and its first playbook."""
    t = starter.BY_KEY.get(key)
    if t is None:
        raise Refused(f"No starter template called {key!r}.")
    return Design(name=t.name, department=t.department, job=t.job, guide=t.guide, tools=list(t.tools), actions=list(t.actions),
                  subscribes=list(t.subscribes), emits=list(t.emits), kpi=t.kpi, max_rounds=t.max_rounds), t.playbook


def create(session: Session, design: Design, *, by: str, template: str = "", playbook: str = "", settings: config.AgentSettings | None = None) -> OwnerAgent:
    """Make an agent. It starts switched off, with every tool of its design granted, until the owner sets the rest."""
    problems = check(design)
    if problems:
        raise Refused(" ".join(problems))
    key = make_key(session, design.name)
    spec = _spec(key, design)
    try:
        wanted = config.check(key, settings or config.AgentSettings(tools=list(registry.tools_of(spec))), spec)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    now = datetime.now(UTC)
    row = OwnerAgent(key=key, name=design.name, department=design.department, job=design.job, guide=design.guide, tools=list(spec.tools),
                     actions=list(spec.actions), subscribes=list(spec.subscribes), emits=list(spec.emits), kpi=design.kpi,
                     max_rounds=design.max_rounds, template=template, status="active", created_by=by[:60], created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    config.save_agent(session, key, wanted, spec)
    if playbook.strip():
        approvals.save_playbook(session, key, playbook.strip(), note="first version", created_by=by[:60])
    registry.AGENTS.remember(spec)
    return row


def get(session: Session, key: str) -> OwnerAgent | None:
    row: OwnerAgent | None = session.execute(select(OwnerAgent).where(OwnerAgent.key == key)).scalar_one_or_none()
    return row


def update(session: Session, key: str, design: Design, *, by: str) -> OwnerAgent:
    """Change an agent's design. Settings that no longer fit the design (a tool taken away) are dropped, never kept."""
    problems = check(design, key=key)
    row = get(session, key)
    if row is None or row.status != "active":
        raise Refused(" ".join(problems) if key in registry.BUILT_IN else "No such agent of yours.")
    if problems:
        raise Refused(" ".join(problems))
    spec = _spec(key, design)
    mine = config.agent(session, key)
    allowed_actions = set(registry.actions_of(spec))
    fitted = mine.model_copy(update={
        "tools": [t for t in mine.tools if t in registry.tools_of(spec)],
        "levels": {a: v for a, v in mine.levels.items() if a in allowed_actions},
        "l2_daily_limit": {a: v for a, v in mine.l2_daily_limit.items() if a in allowed_actions},
        "proposals_per_day": {a: v for a, v in mine.proposals_per_day.items() if a in allowed_actions},
    })
    for field_name in ("name", "department", "job", "guide", "kpi", "max_rounds"):
        setattr(row, field_name, getattr(design, field_name))
    row.tools, row.actions, row.subscribes, row.emits = list(spec.tools), list(spec.actions), list(spec.subscribes), list(spec.emits)
    row.updated_at = datetime.now(UTC)
    config.save_agent(session, key, fitted, spec)
    registry.AGENTS.remember(spec)
    return row


def archive(session: Session, key: str) -> OwnerAgent:
    """Take an agent off the team: switched off at once; its runs, proposals and notes stay for the record."""
    row = get(session, key)
    if row is None or row.status != "active":
        raise Refused("No such agent of yours.")
    mine = config.agent(session, key)
    config.save_agent(session, key, mine.model_copy(update={"enabled": False}), registry.from_row(row))
    for waiting in session.execute(select(TeamApproval).where(TeamApproval.agent == key,
                                                                        TeamApproval.status == "pending")).scalars():
        waiting.status, waiting.decided_at = "withdrawn", datetime.now(UTC)
        waiting.decision_note = "The agent was taken off the team."
    row.status, row.updated_at = "archived", datetime.now(UTC)
    registry.AGENTS.forget(key)
    return row


# --- Nia proposes an agent; the owner clears it ------------------------------------------------------------------------
def _design_of(payload: dict[str, Any]) -> tuple[Design, str, str]:
    base: dict[str, Any] = {}
    playbook = str(payload.get("playbook") or "")
    template = str(payload.get("template") or "")
    if template:
        design, first = from_template(template)
        base, playbook = design.model_dump(), playbook or first
    fields = {k: payload[k] for k in Design.model_fields if k in payload and payload[k] not in (None, "", [])}
    return Design.model_validate({**base, **fields}), template, playbook


def _check_proposal(session: Session, payload: dict[str, Any]) -> list[str]:
    try:
        design, _template, _playbook = _design_of(payload)
    except (Refused, ValueError) as exc:
        return [str(exc)]
    return check(design)


@approvals.executor("agent.create")
def _execute_create(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    design, template, playbook = _design_of(payload)
    row = create(session, design, by=f"{approval.agent} (cleared by the owner)", template=template, playbook=playbook)
    return {"agent": row.key, "name": row.name}


approvals.CHECKS["agent.create"] = _check_proposal
