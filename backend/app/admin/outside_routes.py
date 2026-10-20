"""Admin → Team → Outside agents: hire agents from other providers, give them a
desk, tools and the actions they may propose, issue their credential (shown once), pause, revoke, read what they did."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ..core.crypto import encrypt
from ..core.errors import NotFound, ValidationFailed
from ..models import OutsideAgent, OutsideAgentCall, OutsideAgentMessage, TeamRun
from ..services import platform
from ..services.outside import core, gateway, hosted
from ..services.team import approvals, registry, tools
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api/outside", tags=["admin-outside"])

DESKS = 10  # the outside wing of the office has one desk per possible outside agent


class OutsideIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    job: str = Field(min_length=10, max_length=4000)
    department: str = Field(default="", max_length=40)
    kind: Literal["connected", "hosted"] = "connected"
    provider: Literal["anthropic", "openai", "google", "other"] = "other"
    client_label: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=80)
    # Hosted: the provider's API key. Empty on an edit keeps the one stored.
    provider_key: str = Field(default="", max_length=400)
    tools: list[str] = Field(default_factory=list, max_length=80)
    actions: list[str] = Field(default_factory=list, max_length=40)
    desk: int | None = Field(default=None, ge=1, le=DESKS)
    calls_per_day: int | None = Field(default=None, ge=1, le=100000)
    tokens_per_day: int | None = Field(default=None, ge=1000, le=100_000_000)
    schedule: dict[str, Any] = Field(default_factory=dict)
    ip_allowlist: list[str] = Field(default_factory=list, max_length=20)
    # The owner's full instructions (the long prompt): saved as its playbook. Empty on an edit keeps the current one.
    playbook: str = Field(default="", max_length=20000)

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9._:\-/]+", v):
            raise ValueError("letters, digits, dots, dashes only (e.g. claude-sonnet-5-5, gpt-5, gemini-3-flash)")
        return v


class OutsideAgentOut(BaseModel):
    id: int
    key: str
    name: str
    job: str
    department: str
    kind: str
    provider: str
    client_label: str
    model: str
    has_provider_key: bool
    tools: list[str]
    actions: list[str]
    desk: int | None
    status: str
    calls_per_day: int | None
    tokens_per_day: int | None
    schedule: dict[str, Any]
    ip_allowlist: list[str]
    credential_prefix: str
    credential_issued_at: datetime | None
    credential_expires_at: datetime | None
    last_seen_at: datetime | None
    last_run_at: datetime | None
    calls_today: int
    tokens_today: int
    unread_messages: int
    created_at: datetime


class OutsideOffered(BaseModel):
    key: str
    description: str


class OutsideOverviewOut(BaseModel):
    rules: core.OutsideRules
    agents: list[OutsideAgentOut]
    tools: list[OutsideOffered]
    actions: list[OutsideOffered]
    desks: int
    gateway_url: str


class OutsideCredentialOut(BaseModel):
    credential: str
    setup: dict[str, str]
    agent: OutsideAgentOut


class OutsideCallOut(BaseModel):
    at: datetime
    via: str
    method: str
    tool: str
    ok: bool
    error: str
    args: str
    result_chars: int
    ip: str
    duration_ms: int
    tokens: int


class OutsideRunOut(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary: str
    error: str


class OutsideMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class OutsideMessageOut(BaseModel):
    id: int
    sender: str
    text: str
    created_at: datetime
    read_at: datetime | None


class OutsidePlaybookIn(BaseModel):
    text: str = Field(max_length=20000)
    note: str = Field(default="", max_length=300)


class OutsideDetailOut(BaseModel):
    agent: OutsideAgentOut
    calls: list[OutsideCallOut]
    runs: list[OutsideRunOut]
    messages: list[OutsideMessageOut]
    playbook: str
    proposals_waiting: int


def _day() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _out(session: Any, row: OutsideAgent) -> OutsideAgentOut:
    calls, tokens = session.execute(select(func.count(), func.coalesce(func.sum(OutsideAgentCall.input_tokens + OutsideAgentCall.output_tokens), 0))
                                    .where(OutsideAgentCall.agent_id == row.id, OutsideAgentCall.at >= _day())).one()
    unread = session.execute(select(func.count()).select_from(OutsideAgentMessage).where(OutsideAgentMessage.agent_id == row.id,
                                                                                         OutsideAgentMessage.read_at.is_(None))).scalar() or 0
    return OutsideAgentOut(id=row.id, key=row.key, name=row.name, job=row.job, department=row.department, kind=row.kind, provider=row.provider,
                    client_label=row.client_label, model=row.model, has_provider_key=bool(row.provider_key_ciphertext), tools=row.tools, actions=row.actions,
                    desk=row.desk, status=row.status, calls_per_day=row.calls_per_day, tokens_per_day=row.tokens_per_day, schedule=row.schedule or {},
                    ip_allowlist=row.ip_allowlist or [], credential_prefix=row.credential_prefix, credential_issued_at=row.credential_issued_at,
                    credential_expires_at=row.credential_expires_at, last_seen_at=row.last_seen_at, last_run_at=row.last_run_at,
                    calls_today=int(calls or 0), tokens_today=int(tokens or 0), unread_messages=int(unread), created_at=row.created_at)


def _load(session: Any, agent_id: int) -> OutsideAgent:
    row: OutsideAgent | None = session.get(OutsideAgent, agent_id)
    if row is None:
        raise NotFound("No such outside agent.", code="OUTSIDE_NOT_FOUND")
    return row


def _overview(session: Any) -> OutsideOverviewOut:
    rows = list(session.execute(select(OutsideAgent).order_by(OutsideAgent.id)).scalars())
    return OutsideOverviewOut(rules=core.rules(session), agents=[_out(session, r) for r in rows],
                       tools=[OutsideOffered(key=t, description=tools.CATALOGUE[t][1]) for t in core.offered_tools()],
                       actions=[OutsideOffered(key=a, description=registry.ACTIONS[a].label) for a in core.offered_actions()],
                       desks=DESKS, gateway_url=f"{gateway.public_base()}/agent-gateway/mcp")


def _check(session: Any, data: OutsideIn, row: OutsideAgent | None) -> None:
    from ..config import settings  # noqa: F401 - the encryption key is configured

    wrong_tools = sorted(set(data.tools) - set(core.offered_tools()))
    wrong_actions = sorted(set(data.actions) - set(core.offered_actions()))
    if wrong_tools or wrong_actions:
        raise ValidationFailed(f"Not offered to outside agents: {', '.join(wrong_tools + wrong_actions)}.", code="OUTSIDE_NOT_OFFERED")
    if data.kind == "hosted":
        if data.provider not in hosted.PROVIDERS or not data.model:
            raise ValidationFailed("A hosted agent needs its provider (Anthropic, OpenAI or Google) and the model's name.", code="OUTSIDE_HOSTED")
        if not data.provider_key and not (row is not None and row.provider_key_ciphertext):
            raise ValidationFailed("A hosted agent needs the provider's API key.", code="OUTSIDE_HOSTED_KEY")
        try:
            from ..services.team import config

            config.AgentSchedule.model_validate(data.schedule or {})
        except ValueError as exc:
            raise ValidationFailed(f"Schedule: {exc}", code="OUTSIDE_SCHEDULE") from exc
    if data.desk is not None:
        taken = session.execute(select(OutsideAgent.id).where(OutsideAgent.desk == data.desk, OutsideAgent.status != "revoked",
                                                              OutsideAgent.id != (row.id if row else 0))).first()
        if taken:
            raise ValidationFailed(f"Desk {data.desk} is taken.", code="OUTSIDE_DESK")
    for entry in data.ip_allowlist:
        import ipaddress

        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise ValidationFailed(f"{entry!r} is not an address or network.", code="OUTSIDE_IP") from exc


def _apply(row: OutsideAgent, data: OutsideIn) -> None:
    for name in ("name", "job", "department", "kind", "provider", "client_label", "model", "tools", "actions", "desk", "calls_per_day", "tokens_per_day",
                 "schedule", "ip_allowlist"):
        setattr(row, name, getattr(data, name))
    if data.provider_key:
        row.provider_key_ciphertext = encrypt(data.provider_key.strip(), associated_data=f"{hosted.KEY_CONTEXT}:{row.id}")
    row.updated_at = datetime.now(UTC)


@router.get("", response_model=OutsideOverviewOut)
def overview(_ctx: CurrentAdmin, session: AdminDb) -> OutsideOverviewOut:
    return _overview(session)


@router.put("/rules", response_model=OutsideOverviewOut)
def put_rules(data: core.OutsideRules, ctx: OwnerAdmin, session: AdminDb) -> OutsideOverviewOut:
    """How many outside agents, the pause for all, how long a credential lasts, the daily calls."""
    before = core.rules(session).model_dump()
    platform.put(session, core.KEY, data.model_dump())
    record(session, ctx.admin, "outside.rules_changed", target_type="outside", ip=ctx.ip, metadata={"before": before, "after": data.model_dump()})
    return _overview(session)


@router.post("", response_model=OutsideAgentOut, status_code=201)
def create(data: OutsideIn, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    """Hire an outside agent (no credential yet)."""
    count = session.execute(select(func.count()).select_from(OutsideAgent).where(OutsideAgent.status != "revoked")).scalar() or 0
    limit = core.rules(session).max_agents
    if limit is not None and count >= limit:
        raise ValidationFailed(f"The company has {count} outside agents; the limit is {limit}.", code="OUTSIDE_LIMIT")
    _check(session, data, None)
    key, n = core.make_key(data.name), 2
    while session.execute(select(OutsideAgent.id).where(OutsideAgent.key == key)).first() or key in registry.AGENTS:
        key, n = f"{core.make_key(data.name)[:36]}_{n}", n + 1
    now = datetime.now(UTC)
    row = OutsideAgent(key=key, name="", job="", status="draft", tools=[], actions=[], schedule={}, ip_allowlist=[], provider_key_ciphertext="",
                       created_by=ctx.admin.email, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    _apply(row, data)
    if row.kind == "hosted":
        row.status = "active"
    if data.playbook.strip():
        approvals.save_playbook(session, row.key, data.playbook.strip(), note="with the hiring", created_by=ctx.admin.email)
    record(session, ctx.admin, "outside.hired", target_type="outside_agent", target_id=row.key, ip=ctx.ip,
           metadata={"kind": row.kind, "provider": row.provider, "tools": row.tools, "actions": row.actions})
    return _out(session, row)


@router.get("/{agent_id}", response_model=OutsideDetailOut)
def detail(agent_id: int, _ctx: CurrentAdmin, session: AdminDb, calls: int = Query(default=100, ge=1, le=500)) -> OutsideDetailOut:
    row = _load(session, agent_id)
    call_rows = session.execute(select(OutsideAgentCall).where(OutsideAgentCall.agent_id == row.id).order_by(OutsideAgentCall.id.desc()).limit(calls)).scalars()
    runs = session.execute(select(TeamRun).where(TeamRun.agent == row.key).order_by(TeamRun.id.desc()).limit(30)).scalars()
    messages = session.execute(select(OutsideAgentMessage).where(OutsideAgentMessage.agent_id == row.id).order_by(OutsideAgentMessage.id.desc()).limit(50)).scalars()
    playbook = approvals.latest_playbook(session, row.key)
    from ..models import TeamApproval

    waiting = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.agent == row.key, TeamApproval.status == "pending")).scalar() or 0
    return OutsideDetailOut(agent=_out(session, row),
                     calls=[OutsideCallOut(at=c.at, via=c.via, method=c.method, tool=c.tool, ok=c.ok, error=c.error, args=c.args, result_chars=c.result_chars, ip=c.ip,
                                    duration_ms=c.duration_ms, tokens=c.input_tokens + c.output_tokens) for c in call_rows],
                     runs=[OutsideRunOut(id=r.id, trigger=r.trigger, status=r.status, started_at=r.started_at, finished_at=r.finished_at, summary=r.summary, error=r.error)
                           for r in runs],
                     messages=[OutsideMessageOut(id=m.id, sender=m.sender, text=m.text, created_at=m.created_at, read_at=m.read_at) for m in messages],
                     playbook=playbook.text if playbook else "", proposals_waiting=int(waiting))


@router.put("/{agent_id}", response_model=OutsideAgentOut)
def update(agent_id: int, data: OutsideIn, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    row = _load(session, agent_id)
    if row.status == "revoked":
        raise ValidationFailed("A revoked agent cannot be changed; hire it again.", code="OUTSIDE_REVOKED")
    if data.kind != row.kind:
        raise ValidationFailed("The kind cannot change; hire a new agent.", code="OUTSIDE_KIND")
    _check(session, data, row)
    before = {"tools": row.tools, "actions": row.actions, "desk": row.desk}
    _apply(row, data)
    current = approvals.latest_playbook(session, row.key)
    if data.playbook.strip() and (current is None or current.text != data.playbook.strip()):
        approvals.save_playbook(session, row.key, data.playbook.strip(), note="changed with the agent", created_by=ctx.admin.email)
    record(session, ctx.admin, "outside.changed", target_type="outside_agent", target_id=row.key, ip=ctx.ip,
           metadata={"before": before, "after": {"tools": row.tools, "actions": row.actions, "desk": row.desk}, "key_changed": bool(data.provider_key)})
    return _out(session, row)


class OutsideIssueIn(BaseModel):
    # Valid this many days (empty: the company's rule).
    days: int | None = Field(default=None, ge=1, le=730)


@router.post("/{agent_id}/credential", response_model=OutsideCredentialOut)
def issue(agent_id: int, data: OutsideIssueIn, ctx: OwnerAdmin, session: AdminDb) -> OutsideCredentialOut:
    """Issue (or rotate) the agent's credential. Shown this once; the old one stops at once."""
    row = _load(session, agent_id)
    if row.kind != "connected":
        raise ValidationFailed("A hosted agent works with its provider key; it needs no credential.", code="OUTSIDE_HOSTED_NO_CREDENTIAL")
    rotated = bool(row.credential_hash)
    token = core.issue(session, row, days=data.days or core.rules(session).credential_days)
    record(session, ctx.admin, "outside.credential_rotated" if rotated else "outside.credential_issued", target_type="outside_agent", target_id=row.key,
           ip=ctx.ip, metadata={"prefix": row.credential_prefix, "expires": str(row.credential_expires_at or "")})
    return OutsideCredentialOut(credential=token, setup=gateway.setup(gateway.public_base(), token), agent=_out(session, row))


def _status(session: Any, ctx: Any, agent_id: int, status: str, action: str) -> OutsideAgentOut:
    row = _load(session, agent_id)
    if row.status == "revoked":
        raise ValidationFailed("This agent is revoked.", code="OUTSIDE_REVOKED")
    if status == "paused":
        row.status = "paused"
    elif status == "resume":
        row.status = "active" if (row.kind == "hosted" or row.last_seen_at) else ("waiting" if row.credential_hash else "draft")
    row.updated_at = datetime.now(UTC)
    record(session, ctx.admin, action, target_type="outside_agent", target_id=row.key, ip=ctx.ip)
    return _out(session, row)


@router.post("/{agent_id}/pause", response_model=OutsideAgentOut)
def pause(agent_id: int, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    return _status(session, ctx, agent_id, "paused", "outside.paused")


@router.post("/{agent_id}/resume", response_model=OutsideAgentOut)
def resume(agent_id: int, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    return _status(session, ctx, agent_id, "resume", "outside.resumed")


@router.post("/{agent_id}/revoke", response_model=OutsideAgentOut)
def revoke(agent_id: int, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    """Revoke: the credential and every connector sign-in stop at once; the desk is freed."""
    row = _load(session, agent_id)
    core.revoke(session, row)
    row.desk = None
    record(session, ctx.admin, "outside.revoked", target_type="outside_agent", target_id=row.key, ip=ctx.ip)
    return _out(session, row)


@router.delete("/{agent_id}", status_code=204)
def delete(agent_id: int, ctx: OwnerAdmin, session: AdminDb) -> None:
    """Remove a draft or revoked agent with its call log (its runs and proposals stay in the team's history)."""
    row = _load(session, agent_id)
    if row.status not in ("draft", "revoked"):
        raise ValidationFailed("Revoke the agent first.", code="OUTSIDE_ACTIVE")
    record(session, ctx.admin, "outside.removed", target_type="outside_agent", target_id=row.key, ip=ctx.ip)
    session.delete(row)


@router.post("/{agent_id}/messages", response_model=OutsideMessageOut, status_code=201)
def message(agent_id: int, data: OutsideMessageIn, ctx: OwnerAdmin, session: AdminDb) -> OutsideMessageOut:
    """A message for the agent: it reads it with my_messages the next time it works."""
    row = _load(session, agent_id)
    m = OutsideAgentMessage(agent_id=row.id, sender="owner", text=data.text.strip(), created_at=datetime.now(UTC))
    session.add(m)
    session.flush()
    record(session, ctx.admin, "outside.messaged", target_type="outside_agent", target_id=row.key, ip=ctx.ip)
    return OutsideMessageOut(id=m.id, sender=m.sender, text=m.text, created_at=m.created_at, read_at=m.read_at)


@router.put("/{agent_id}/playbook", response_model=OutsideDetailOut)
def playbook(agent_id: int, data: OutsidePlaybookIn, ctx: OwnerAdmin, session: AdminDb) -> OutsideDetailOut:
    """The owner's standing instructions for this agent (my_job and every hosted run carry them)."""
    row = _load(session, agent_id)
    saved = approvals.save_playbook(session, row.key, data.text, note=data.note, created_by=ctx.admin.email)
    record(session, ctx.admin, "outside.playbook_changed", target_type="outside_agent", target_id=row.key, ip=ctx.ip, metadata={"version": saved.version})
    return detail(agent_id, ctx, session, calls=100)


@router.post("/{agent_id}/run", response_model=OutsideAgentOut)
def run_now(agent_id: int, ctx: OwnerAdmin, session: AdminDb) -> OutsideAgentOut:
    """Start a hosted agent's working session now."""
    row = _load(session, agent_id)
    if row.kind != "hosted" or row.status not in ("active", "waiting"):
        raise ValidationFailed("Only an active hosted agent can be started; a connected one works when its operator starts it.", code="OUTSIDE_NOT_HOSTED")
    queued = hosted.start(session, row, "manual")
    record(session, ctx.admin, "outside.run_started", target_type="outside_agent", target_id=row.key, ip=ctx.ip, metadata={"queued": queued})
    return _out(session, row)
