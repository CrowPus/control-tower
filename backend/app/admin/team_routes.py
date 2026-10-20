"""The agent team in the admin dashboard: Agents, Approvals,
Mailboxes. Both admin roles see the team's work; only owners change settings, decide
approvals, edit playbooks and notes, and start runs. Every change is in the admin log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core.errors import NotFound, ValidationFailed
from ..db import admin_transaction
from ..models import (
    OwnerAgent,
    TeamApproval,
    TeamAttachment,
    TeamConversation,
    TeamEvent,
    TeamMessage,
    TeamPlaybook,
    TeamRun,
)
from ..services.insights import queries as insight_queries
from ..services.team import (
    admin_changes,
    agents,
    approvals,
    attachments,
    config,
    events,
    mailboxes,
    registry,
    tasks,
    templates,
    tools,
)
from ..services.team import chat as agent_chat
from ..services.team import costs as team_costs
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin, OwnerAdminBrief

router = APIRouter(prefix="/admin-api/team", tags=["admin-team"])


class TeamAgentRow(BaseModel):
    key: str
    name: str
    department: str
    purpose: str
    kpi: str
    status: str  # unavailable | off | waiting | ready
    waiting_for: list[str]
    # The owner's schedule in one sentence, and why it can never start the agent (when it cannot).
    schedule: str
    schedule_problem: str
    tier: str | None
    last_run_at: datetime | None
    last_run_status: str | None
    cost_today_usd: float
    cost_7d_usd: float
    pending_approvals: int
    autonomy_offers: list[str]


class TeamOverviewOut(BaseModel):
    team: config.TeamSettings
    departments: list[str]
    agents: list[TeamAgentRow]
    pending_approvals: int
    # Everything the team's AI work cost (the agents, Nia, pictures, videos), and its parts.
    cost_today_usd: float
    cost_month_usd: float
    cost_today: dict[str, float] = {}
    cost_month: dict[str, float] = {}


class TeamToolOut(BaseModel):
    key: str
    description: str


class TeamActionOut(BaseModel):
    key: str
    label: str
    ceiling: str
    level: str
    approved_in_a_row: int


class TeamPlaybookOut(BaseModel):
    version: int
    text: str
    note: str
    created_by: str
    created_at: datetime


class TeamNote(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    evidence: str = Field(default="", max_length=600)
    at: str = ""


class TeamRunOut(BaseModel):
    id: int
    agent: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary: str
    waiting_for: list[str]
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str


class TeamAgentDetailOut(BaseModel):
    agent: TeamAgentRow
    # The owner's agents: their design (the agent template, as saved) and the starter template it began from.
    # Built-in agents (Nia) have none: their design is part of Control Tower.
    design: agents.Design | None = None
    template: str = ""
    guide: str = ""
    settings: config.AgentSettings
    tools: list[TeamToolOut]
    actions: list[TeamActionOut]
    subscribes: list[str]
    emits: list[str]
    playbook: TeamPlaybookOut | None
    playbook_versions: list[TeamPlaybookOut]
    notes: list[TeamNote]
    runs: list[TeamRunOut]


class TeamApprovalOut(BaseModel):
    id: int
    agent: str
    agent_name: str
    action_type: str
    action_label: str
    level: str
    title: str
    reason: str
    expected_effect: str
    evidence: list[dict[str, Any]]
    payload: dict[str, Any]
    edited_payload: dict[str, Any] | None
    group_key: str
    status: str
    decided_at: datetime | None
    decision_note: str
    executed_at: datetime | None
    result: dict[str, Any]
    run_id: int | None
    created_at: datetime
    # For a waiting proposal: why it can no longer be carried out as proposed (a duplicate company, a company already written to).
    warnings: list[str] = []


class TeamApprovalsOut(BaseModel):
    items: list[TeamApprovalOut]
    counts: dict[str, int]
    # Proposals that could no longer be carried out as proposed (e.g. the listing was published meanwhile): withdrawn, with why.
    withdrawn: list[str] = []


class TeamDecisionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)
    edited_payload: dict[str, Any] | None = None


class TeamPlaybookIn(BaseModel):
    text: str = Field(max_length=20000)
    note: str = Field(default="", max_length=300)


class TeamTemplateOut(BaseModel):
    key: str
    name: str
    department: str
    design: agents.Design
    playbook: str
    tags: list[str]


class TeamKpiOut(BaseModel):
    key: str
    label: str


class TeamCatalogueOut(BaseModel):
    """Everything the agent template is made of, and the rules a design must keep."""

    templates: list[TeamTemplateOut]
    tools: list[TeamToolOut]
    common_tools: list[TeamToolOut]
    actions: list[TeamActionOut]
    kpis: list[TeamKpiOut]
    departments: list[str]
    min_job: int
    max_rounds: int
    max_events: int


class TeamNewAgentIn(BaseModel):
    # A starter template's key, or empty for a blank design; the design as the owner edited it.
    template: str = Field(default="", max_length=40)
    design: agents.Design
    playbook: str = Field(default="", max_length=20000)


class TeamNotesIn(BaseModel):
    notes: list[TeamNote] = Field(max_length=tools.NOTES_KEPT)


class TeamOfferIn(BaseModel):
    accept: bool
    daily_limit: int | None = Field(default=None, ge=1)


class TeamEventOut(BaseModel):
    id: int
    type: str
    source: str
    payload: dict[str, Any]
    created_at: datetime


class TeamMailboxIn(mailboxes.Mailbox):
    # None keeps the stored password, "" removes it.
    password: str | None = Field(default=None, max_length=500)


class TeamMailboxTestOut(BaseModel):
    smtp: str
    imap: str


class TeamStartOut(BaseModel):
    queued: bool


def _day_start() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _schedule_problem(schedule: config.AgentSchedule) -> str:
    if schedule.weekdays and not schedule.times and not schedule.every_minutes:
        return "Days are ticked but there is no time or 'every … minutes', so it never starts on its own. Add a time."
    return ""


def _row(session: AdminDb, agent: registry.Agent) -> TeamAgentRow:
    status, waiting = config.status(session, agent.key)
    mine = config.agent(session, agent.key)
    last = session.execute(select(TeamRun.started_at, TeamRun.status).where(TeamRun.agent == agent.key).order_by(TeamRun.started_at.desc()).limit(1)).first()
    pending = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.agent == agent.key, TeamApproval.status == "pending")).scalar() or 0
    state = events.state(session, agent.key)
    return TeamAgentRow(
        key=agent.key, name=agent.name, department=agent.department, purpose=agent.purpose, kpi=agent.kpi,
        status=status, waiting_for=waiting, schedule=config.schedule_text(mine.schedule), schedule_problem=_schedule_problem(mine.schedule), tier=mine.tier, last_run_at=last.started_at if last else None, last_run_status=last.status if last else None,
        # Its own work, the pictures it made and (Graphic Designer) its videos (services/team/costs.py).
        cost_today_usd=team_costs.agent(session, agent.key, _day_start()),
        cost_7d_usd=team_costs.agent(session, agent.key, _day_start() - timedelta(days=6)),
        pending_approvals=int(pending), autonomy_offers=list(state.autonomy_offers) if state else [],
    )


def _agent(key: str) -> registry.Agent:
    agent = registry.get(key)
    if agent is None:
        raise NotFound("No such agent.", code="AGENT_NOT_FOUND")
    return agent


def _playbook(row: TeamPlaybook) -> TeamPlaybookOut:
    return TeamPlaybookOut(version=row.version, text=row.text, note=row.note, created_by=row.created_by, created_at=row.created_at)


def _run(row: TeamRun) -> TeamRunOut:
    return TeamRunOut(id=row.id, agent=row.agent, trigger=row.trigger, status=row.status, started_at=row.started_at, finished_at=row.finished_at,
                      summary=row.summary, waiting_for=row.waiting_for, tool_calls=row.tool_calls, input_tokens=row.input_tokens,
                      output_tokens=row.output_tokens, cost_usd=row.cost_usd, error=row.error)


def _approval(row: TeamApproval, session: Any = None) -> TeamApprovalOut:
    agent = registry.get(row.agent)
    if agent is None and session is not None and row.agent.startswith("ext_"):
        from ..services.outside import core as outside

        agent = outside.spec_of(session, row.agent)
    warnings = approvals.recheck(session, row) if session is not None and row.status == "pending" else []
    action = registry.ACTIONS.get(row.action_type)
    return TeamApprovalOut(
        id=row.id, agent=row.agent, agent_name=agent.name if agent else row.agent, action_type=row.action_type,
        action_label=action.label if action else row.action_type, level=row.level, title=row.title, reason=row.reason,
        expected_effect=row.expected_effect, evidence=row.evidence, payload=row.payload, edited_payload=row.edited_payload,
        group_key=row.group_key, status=row.status, decided_at=row.decided_at, decision_note=row.decision_note,
        executed_at=row.executed_at, result=row.result, run_id=row.run_id, created_at=row.created_at, warnings=warnings,
    )


def _overview(session: AdminDb) -> TeamOverviewOut:
    today, month = team_costs.team(session, _day_start()), team_costs.team(session, _day_start().replace(day=1))
    pending = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.status == "pending")).scalar() or 0
    return TeamOverviewOut(
        team=config.team(session), departments=registry.departments(), agents=[_row(session, a) for a in registry.AGENTS.values()],
        pending_approvals=int(pending), cost_today_usd=today["total"], cost_month_usd=month["total"], cost_today=today, cost_month=month,
    )


@router.get("", response_model=TeamOverviewOut)
def team_overview(_ctx: CurrentAdmin, session: AdminDb) -> TeamOverviewOut:
    return _overview(session)


@router.put("/settings", response_model=TeamOverviewOut)
def put_team_settings(data: config.TeamSettings, ctx: OwnerAdmin, session: AdminDb) -> TeamOverviewOut:
    before = config.team(session).model_dump()
    config.save_team(session, data)
    session.flush()
    record(session, ctx.admin, "team.settings_changed", target_type="team", ip=ctx.ip, metadata={"before": before, "after": data.model_dump()})
    return _overview(session)


def _detail(session: AdminDb, agent: registry.Agent) -> TeamAgentDetailOut:
    mine = config.agent(session, agent.key)
    versions = list(session.execute(select(TeamPlaybook).where(TeamPlaybook.agent == agent.key).order_by(TeamPlaybook.version.desc()).limit(30)).scalars())
    runs = list(session.execute(select(TeamRun).where(TeamRun.agent == agent.key).order_by(TeamRun.started_at.desc()).limit(20)).scalars())
    notes = [TeamNote.model_validate(n) for n in events.state(session, agent.key).notes]
    owned = session.execute(select(OwnerAgent).where(OwnerAgent.key == agent.key)).scalar_one_or_none()
    design = agents.Design(name=agent.name, department=agent.department, job=agent.purpose, guide=agent.guide, tools=list(agent.tools),
                           actions=list(agent.actions), subscribes=list(agent.subscribes), emits=list(agent.emits), kpi=agent.kpi,
                           max_rounds=agent.max_rounds) if owned is not None else None
    return TeamAgentDetailOut(
        agent=_row(session, agent), design=design, template=owned.template if owned else "", guide=agent.guide, settings=mine,
        tools=[TeamToolOut(key=t, description=tools.CATALOGUE[t][1]) for t in registry.tools_of(agent) if t in tools.CATALOGUE],
        actions=[TeamActionOut(key=a, label=registry.ACTIONS[a].label, ceiling=registry.ACTIONS[a].ceiling, level=config.level_of(mine, a),
                               approved_in_a_row=approvals.streak(session, agent.key, a)) for a in registry.actions_of(agent)],
        subscribes=list(agent.subscribes), emits=list(agent.emits),
        playbook=_playbook(versions[0]) if versions else None, playbook_versions=[_playbook(v) for v in versions],
        notes=notes, runs=[_run(r) for r in runs],
    )


@router.get("/catalogue", response_model=TeamCatalogueOut)
def team_catalogue(_ctx: CurrentAdmin) -> TeamCatalogueOut:
    def template(t: templates.Template) -> TeamTemplateOut:
        design, playbook = agents.from_template(t.key)
        return TeamTemplateOut(key=t.key, name=t.name, department=t.department, design=design, playbook=playbook, tags=list(t.tags))

    return TeamCatalogueOut(
        templates=[template(t) for t in templates.TEMPLATES],
        tools=[TeamToolOut(key=k, description=tools.CATALOGUE[k][1]) for k in agents.offered_tools()],
        common_tools=[TeamToolOut(key=k, description=tools.CATALOGUE[k][1]) for k in registry.COMMON_TOOLS if k in tools.CATALOGUE],
        actions=[TeamActionOut(key=k, label=registry.ACTIONS[k].label, ceiling=registry.ACTIONS[k].ceiling, level="L1", approved_in_a_row=0)
                 for k in agents.offered_actions()],
        kpis=[TeamKpiOut(key=k.key, label=k.label) for k in insight_queries.KPIS],
        departments=registry.departments(), min_job=agents.MIN_JOB, max_rounds=agents.MAX_ROUNDS, max_events=agents.MAX_EVENTS,
    )


@router.post("/agents", response_model=TeamAgentDetailOut)
def create_team_agent(data: TeamNewAgentIn, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    if data.template and data.template not in templates.BY_KEY:
        raise ValidationFailed("No such starter template.", code="INVALID_AGENT_DESIGN")
    try:
        row = agents.create(session, data.design, by=ctx.admin.email, template=data.template, playbook=data.playbook)
    except agents.Refused as exc:
        raise ValidationFailed(str(exc), code="INVALID_AGENT_DESIGN") from exc
    record(session, ctx.admin, "team.agent_created", target_type="team_agent", target_id=row.key, ip=ctx.ip,
           metadata={"template": data.template, "design": data.design.model_dump()})
    return _detail(session, registry.from_row(row))


@router.put("/agents/{key}/design", response_model=TeamAgentDetailOut)
def put_team_design(key: str, data: agents.Design, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    before = agents.get(session, key)
    if before is None or before.status != "active":
        raise NotFound("No such agent of yours.", code="AGENT_NOT_FOUND")
    old = registry.from_row(before)
    try:
        row = agents.update(session, key, data, by=ctx.admin.email)
    except agents.Refused as exc:
        raise ValidationFailed(str(exc), code="INVALID_AGENT_DESIGN") from exc
    record(session, ctx.admin, "team.agent_redesigned", target_type="team_agent", target_id=key, ip=ctx.ip,
           metadata={"before": {"name": old.name, "tools": list(old.tools), "actions": list(old.actions)}, "after": data.model_dump()})
    return _detail(session, registry.from_row(row))


@router.delete("/agents/{key}", response_model=TeamOverviewOut)
def archive_team_agent(key: str, ctx: OwnerAdmin, session: AdminDb) -> TeamOverviewOut:
    try:
        agents.archive(session, key)
    except agents.Refused as exc:
        raise NotFound(str(exc), code="AGENT_NOT_FOUND") from exc
    record(session, ctx.admin, "team.agent_archived", target_type="team_agent", target_id=key, ip=ctx.ip)
    return _overview(session)


@router.get("/agents/{key}", response_model=TeamAgentDetailOut)
def team_agent(key: str, _ctx: CurrentAdmin, session: AdminDb) -> TeamAgentDetailOut:
    return _detail(session, _agent(key))


@router.put("/agents/{key}", response_model=TeamAgentDetailOut)
def put_team_agent(key: str, data: config.AgentSettings, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    agent = _agent(key)
    before = config.agent(session, key).model_dump()
    try:
        config.save_agent(session, key, data)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="INVALID_AGENT_SETTINGS") from exc
    session.flush()
    record(session, ctx.admin, "team.agent_changed", target_type="team_agent", target_id=key, ip=ctx.ip, metadata={"before": before, "after": data.model_dump()})
    return _detail(session, agent)


@router.put("/agents/{key}/playbook", response_model=TeamAgentDetailOut)
def put_team_playbook(key: str, data: TeamPlaybookIn, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    agent = _agent(key)
    row = approvals.save_playbook(session, key, data.text, note=data.note, created_by=ctx.admin.email)
    record(session, ctx.admin, "team.playbook_changed", target_type="team_agent", target_id=key, ip=ctx.ip, metadata={"version": row.version})
    return _detail(session, agent)


@router.put("/agents/{key}/notes", response_model=TeamAgentDetailOut)
def put_team_notes(key: str, data: TeamNotesIn, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    agent = _agent(key)
    state = events.state(session, key)
    before = len(state.notes)
    state.notes = [n.model_dump() for n in data.notes]
    record(session, ctx.admin, "team.notes_changed", target_type="team_agent", target_id=key, ip=ctx.ip, metadata={"before": before, "after": len(data.notes)})
    return _detail(session, agent)


@router.post("/agents/{key}/offers/{action_type}", response_model=TeamAgentDetailOut)
def answer_team_offer(key: str, action_type: str, data: TeamOfferIn, ctx: OwnerAdmin, session: AdminDb) -> TeamAgentDetailOut:
    agent = _agent(key)
    try:
        approvals.answer_offer(session, key, action_type, accept=data.accept, daily_limit=data.daily_limit)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="INVALID_OFFER_ANSWER") from exc
    record(session, ctx.admin, "team.autonomy_answered", target_type="team_agent", target_id=key, ip=ctx.ip,
           metadata={"action_type": action_type, "accept": data.accept, "daily_limit": data.daily_limit})
    return _detail(session, agent)


@router.post("/agents/{key}/run", response_model=TeamStartOut)
def start_team_run(key: str, ctx: OwnerAdmin, session: AdminDb) -> TeamStartOut:
    agent = _agent(key)
    status, waiting = config.status(session, key)
    if status != "ready":
        raise ValidationFailed(
            {"unavailable": f"{agent.name} is not part of the team.", "off": f"{agent.name} is switched off."}.get(status, "Waiting for: " + ", ".join(waiting)),
            code="AGENT_NOT_READY",
        )
    queued = tasks.start(session, key, "manual")
    record(session, ctx.admin, "team.run_started", target_type="team_agent", target_id=key, ip=ctx.ip, metadata={"queued": queued})
    return TeamStartOut(queued=queued)


@router.get("/runs/{run_id}", response_model=TeamRunOut)
def team_run(run_id: int, _ctx: CurrentAdmin, session: AdminDb) -> TeamRunOut:
    row = session.get(TeamRun, run_id)
    if row is None:
        raise NotFound("No such run.", code="RUN_NOT_FOUND")
    return _run(row)


def _counts(session: AdminDb) -> dict[str, int]:
    return {str(status): int(n) for status, n in session.execute(select(TeamApproval.status, func.count()).group_by(TeamApproval.status)).all()}


@router.get("/approvals", response_model=TeamApprovalsOut)
def team_approvals(_ctx: CurrentAdmin, session: AdminDb, status: str = Query(default="pending", max_length=10), agent: str = Query(default="", max_length=40),
                   limit: int = Query(default=200, ge=1, le=500)) -> TeamApprovalsOut:
    stmt = select(TeamApproval).order_by(TeamApproval.id.desc()).limit(limit)
    if status != "all":
        stmt = stmt.where(TeamApproval.status == status)
    if agent:
        stmt = stmt.where(TeamApproval.agent == agent)
    return TeamApprovalsOut(items=[_approval(r, session) for r in session.execute(stmt).scalars()], counts=_counts(session))


@router.post("/approvals/decide", response_model=TeamApprovalsOut)
def decide_team_approvals(data: TeamDecisionIn, ctx: OwnerAdmin, session: AdminDb) -> TeamApprovalsOut:
    rows = list(session.execute(select(TeamApproval).where(TeamApproval.id.in_(data.ids))).scalars())
    if len(rows) != len(set(data.ids)):
        raise NotFound("A proposal was not found.", code="APPROVAL_NOT_FOUND")
    if data.decision == "approve" and any(r.action_type == admin_changes.ACTION for r in rows):
        raise ValidationFailed("Approve Nia's admin changes one by one on their card: your admin page carries them out.", code="ADMIN_CHANGE_ON_CARD")
    withdrawn: list[str] = []
    if data.decision == "approve" and data.edited_payload is None:
        # A duplicate or outdated proposal is never approved. On its own card that is an error;
        # in a batch it is withdrawn with why, and the rest is approved, so one stale card never blocks a hundred good ones.
        stale = [(r, approvals.recheck(session, r)) for r in rows if r.status == "pending"]
        stale = [(r, p) for r, p in stale if p]
        if stale and len(rows) == 1:
            raise ValidationFailed(" ".join(f"#{r.id}: {' '.join(p)}" for r, p in stale), code="DUPLICATE_PROPOSAL")
        if stale:
            gone = {r.id for r, _p in stale}
            approvals.withdraw_stale(session)
            for r, p in stale:
                if r.status == "pending":  # a type without a recheck run: withdrawn here
                    r.status, r.decided_at, r.decision_note = "withdrawn", datetime.now(UTC), ("Withdrawn when approving a batch: " + " ".join(p))[:2000]
                withdrawn.append(f"#{r.id}: {' '.join(p)}")
            rows = [r for r in rows if r.id not in gone]
            if not rows:
                return TeamApprovalsOut(items=[], counts=_counts(session), withdrawn=withdrawn)
    try:
        changed = approvals.decide(session, rows, data.decision, admin_id=ctx.admin.id, note=data.note, edited_payload=data.edited_payload)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="INVALID_DECISION") from exc
    record(session, ctx.admin, f"team.approval_{data.decision}d", target_type="team_approval", target_id=",".join(str(r.id) for r in changed), ip=ctx.ip,
           metadata={"count": len(changed), "edited": data.edited_payload is not None})
    return TeamApprovalsOut(items=[_approval(r, session) for r in rows], counts=_counts(session), withdrawn=withdrawn)


# --- Nia's admin changes (services/team/admin_changes.py): the owner's browser sends them ----------------
class AdminChangeIn(BaseModel):
    # The proposal as the owner approves it (edited on the card, or as proposed).
    payload: dict[str, Any] | None = None


class AdminChangeOut(BaseModel):
    method: str
    path: str
    query: dict[str, Any]
    body: Any = None


class AdminChangeDoneIn(AdminChangeIn):
    status_code: int = Field(ge=0, le=599)
    answer: Any = None
    note: str = Field(default="", max_length=2000)


def _admin_change(session: AdminDb, approval_id: int) -> TeamApproval:
    row = session.get(TeamApproval, approval_id)
    if row is None or row.action_type != admin_changes.ACTION:
        raise NotFound("No such admin change.", code="APPROVAL_NOT_FOUND")
    if row.status != "pending":
        raise ValidationFailed("This proposal was already decided.", code="APPROVAL_DECIDED")
    return row


def _clean(row: TeamApproval, payload: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return admin_changes.check(payload if payload is not None else row.payload)
    except admin_changes.Refused as exc:
        raise ValidationFailed(str(exc), code="ADMIN_CHANGE_REFUSED") from exc


@router.post("/approvals/{approval_id}/admin-change/check", response_model=AdminChangeOut)
def check_admin_change(approval_id: int, data: AdminChangeIn, _ctx: OwnerAdmin, session: AdminDb) -> AdminChangeOut:
    """Right before the owner's page sends it: the request in its checked form."""
    return AdminChangeOut(**_clean(_admin_change(session, approval_id), data.payload))


@router.post("/approvals/{approval_id}/admin-change/done", response_model=TeamApprovalOut)
def admin_change_done(approval_id: int, data: AdminChangeDoneIn, ctx: OwnerAdmin, session: AdminDb) -> TeamApprovalOut:
    """The owner's page sent the approved change; this records the decision and what came back."""
    row = _admin_change(session, approval_id)
    clean = _clean(row, data.payload)
    now = datetime.now(UTC)
    ok = 200 <= data.status_code < 300
    row.status, row.decided_by_admin_id, row.decided_at, row.executed_at = ("executed" if ok else "failed"), ctx.admin.id, now, now
    row.decision_note = data.note
    if clean != row.payload:
        row.edited_payload = clean
    answer = data.answer if len(str(data.answer)) <= 4000 else str(data.answer)[:4000]
    row.result = {"status": data.status_code, "answer": answer}
    events.emit(session, "approval.decided", "owner", {"agent": row.agent, "approval_id": row.id, "action_type": row.action_type, "decision": "approved",
                                                      "edited": row.edited_payload is not None, "note": row.decision_note})
    events.emit(session, "approval.executed", "owner", {"agent": row.agent, "approval_id": row.id, "action_type": row.action_type, "status": row.status})
    record(session, ctx.admin, "team.admin_change_carried_out", target_type="team_approval", target_id=row.id, ip=ctx.ip, result="ok" if ok else "failed",
           metadata={"agent": row.agent, "method": clean["method"], "path": clean["path"], "status": data.status_code})
    return _approval(row)


@router.get("/events", response_model=list[TeamEventOut])
def team_events(_ctx: CurrentAdmin, session: AdminDb, limit: int = Query(default=100, ge=1, le=500)) -> list[TeamEventOut]:
    rows = session.execute(select(TeamEvent).order_by(TeamEvent.id.desc()).limit(limit)).scalars()
    return [TeamEventOut(id=e.id, type=e.type, source=e.source, payload=e.payload, created_at=e.created_at) for e in rows]


@router.get("/mailboxes", response_model=list[mailboxes.MailboxView])
def team_mailboxes(_ctx: CurrentAdmin, session: AdminDb) -> list[mailboxes.MailboxView]:
    return [mailboxes.view(session, name) for name in mailboxes.NAMES]


@router.put("/mailboxes/{name}", response_model=mailboxes.MailboxView)
def put_team_mailbox(name: str, data: TeamMailboxIn, ctx: OwnerAdmin, session: AdminDb) -> mailboxes.MailboxView:
    if name not in mailboxes.NAMES:
        raise NotFound("No such mailbox.", code="MAILBOX_NOT_FOUND")
    box = mailboxes.Mailbox.model_validate(data.model_dump(exclude={"password"}))
    before = mailboxes.view(session, name).model_dump(exclude={"missing"})
    out = mailboxes.save(session, name, box, password=data.password)
    record(session, ctx.admin, "team.mailbox_changed", target_type="mailbox", target_id=name, ip=ctx.ip,
           metadata={"before": before, "after": out.model_dump(exclude={"missing"}), "password_changed": data.password is not None})
    return out


@router.post("/mailboxes/{name}/test", response_model=TeamMailboxTestOut)
def test_team_mailbox(name: str, ctx: OwnerAdmin, session: AdminDb) -> TeamMailboxTestOut:
    if name not in mailboxes.NAMES:
        raise NotFound("No such mailbox.", code="MAILBOX_NOT_FOUND")
    result = mailboxes.test(session, name)
    record(session, ctx.admin, "team.mailbox_tested", target_type="mailbox", target_id=name, ip=ctx.ip, metadata=result)
    return TeamMailboxTestOut(**result)


# --- talking to an agent -------------------------------
class TeamConversationOut(BaseModel):
    id: int
    title: str
    updated_at: datetime


class TeamChatOut(BaseModel):
    # Empty when the agent can answer; otherwise why not (switched off, a setting, a budget).
    problem: str
    conversations: list[TeamConversationOut]


class TeamAttachmentOut(BaseModel):
    id: int
    name: str
    mime_type: str
    size: int
    created_at: datetime


class TeamMessageOut(BaseModel):
    id: int
    role: str  # owner | agent
    text: str
    attachments: list[TeamAttachmentOut]
    proposals: list[TeamApprovalOut]
    tools: list[str]
    cost_usd: float
    created_at: datetime


class TeamConversationDetail(BaseModel):
    id: int
    agent: str
    title: str
    messages: list[TeamMessageOut]


class TeamSayIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=10)


def _attachment(row: TeamAttachment) -> TeamAttachmentOut:
    return TeamAttachmentOut(id=row.id, name=row.name, mime_type=row.mime_type, size=row.size, created_at=row.created_at)


def _conversation_of(session: AdminDb, key: str, conversation_id: int) -> TeamConversation:
    _agent(key)
    row = session.get(TeamConversation, conversation_id)
    if row is None or row.agent != key:
        raise NotFound("No such conversation.", code="CONVERSATION_NOT_FOUND")
    return row


def _conversation_detail(session: AdminDb, row: TeamConversation) -> TeamConversationDetail:
    messages = list(session.execute(select(TeamMessage).where(TeamMessage.conversation_id == row.id).order_by(TeamMessage.id)).scalars())
    file_ids = {i for m in messages for i in m.attachment_ids}
    proposal_ids = {i for m in messages for i in m.proposal_ids}
    files = {a.id: _attachment(a) for a in session.execute(select(TeamAttachment).where(TeamAttachment.id.in_(file_ids))).scalars()} if file_ids else {}
    proposals = {a.id: _approval(a) for a in session.execute(select(TeamApproval).where(TeamApproval.id.in_(proposal_ids))).scalars()} if proposal_ids else {}
    return TeamConversationDetail(id=row.id, agent=row.agent, title=row.title or "New conversation", messages=[
        TeamMessageOut(id=m.id, role=m.role, text=m.text, attachments=[files[i] for i in m.attachment_ids if i in files],
                       proposals=[proposals[i] for i in m.proposal_ids if i in proposals], tools=[c.get("tool", "") for c in m.tool_calls],
                       cost_usd=m.cost_usd, created_at=m.created_at) for m in messages])


@router.get("/agents/{key}/chat", response_model=TeamChatOut)
def team_chat(key: str, _ctx: CurrentAdmin, session: AdminDb) -> TeamChatOut:
    """Whether the agent can talk now, and its conversations with the owner (newest first)."""
    _agent(key)
    rows = session.execute(select(TeamConversation).where(TeamConversation.agent == key).order_by(TeamConversation.updated_at.desc()).limit(100)).scalars()
    return TeamChatOut(problem=agent_chat.ready(session, key),
                       conversations=[TeamConversationOut(id=c.id, title=c.title or "New conversation", updated_at=c.updated_at) for c in rows])


@router.post("/agents/{key}/conversations", response_model=TeamConversationDetail, status_code=201)
def team_new_conversation(key: str, ctx: OwnerAdmin, session: AdminDb) -> TeamConversationDetail:
    """Start a new conversation with the agent."""
    _agent(key)
    if key == "nia":
        raise ValidationFailed("Talk to Nia on her own page (Admin → Nia).", code="USE_NIA_PAGE")
    now = datetime.now(UTC)
    row = TeamConversation(agent=key, title="", admin_id=ctx.admin.id, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _conversation_detail(session, row)


@router.get("/agents/{key}/conversations/{conversation_id}", response_model=TeamConversationDetail)
def team_conversation(key: str, conversation_id: int, _ctx: CurrentAdmin, session: AdminDb) -> TeamConversationDetail:
    """One conversation: every message, its files and the proposals made in it (with their status now)."""
    return _conversation_detail(session, _conversation_of(session, key, conversation_id))


@router.delete("/agents/{key}/conversations/{conversation_id}", status_code=204)
def team_delete_conversation(key: str, conversation_id: int, ctx: OwnerAdmin, session: AdminDb) -> None:
    """Delete a conversation with its messages and files (proposals made in it stay in Approvals)."""
    row = _conversation_of(session, key, conversation_id)
    record(session, ctx.admin, "team.conversation_deleted", target_type="team_conversation", target_id=row.id, ip=ctx.ip, metadata={"agent": key})
    session.delete(row)


@router.post("/agents/{key}/conversations/{conversation_id}/files", response_model=TeamAttachmentOut, status_code=201)
async def team_attach(key: str, conversation_id: int, ctx: OwnerAdmin, session: AdminDb, file: UploadFile = File(...)) -> TeamAttachmentOut:  # noqa: B008 - FastAPI's form parameter
    """Attach a file (CSV, Excel, PDF, text, image): checked, scanned and kept with the conversation until it is sent with a message."""
    _conversation_of(session, key, conversation_id)
    data = await file.read(attachments.MAX_BYTES + 1)
    try:
        row = attachments.store(session, conversation_id, file.filename or "file", data)
    except attachments.Refused as exc:
        raise ValidationFailed(str(exc), code="FILE_REFUSED") from exc
    record(session, ctx.admin, "team.file_attached", target_type="team_conversation", target_id=conversation_id, ip=ctx.ip,
           metadata={"agent": key, "file": row.id, "name": row.name, "size": row.size, "scan": row.scan})
    return _attachment(row)


@router.get("/agents/{key}/conversations/{conversation_id}/files/{file_id}")
def team_file(key: str, conversation_id: int, file_id: int, ctx: OwnerAdmin, session: AdminDb) -> Response:
    """Download a file attached in the conversation."""
    _conversation_of(session, key, conversation_id)
    row = session.get(TeamAttachment, file_id)
    if row is None or row.conversation_id != conversation_id:
        raise NotFound("No such file.", code="FILE_NOT_FOUND")
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in row.name) or "file"
    return Response(content=row.data, media_type=row.mime_type, headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"})


@router.post("/agents/{key}/conversations/{conversation_id}/messages", response_model=TeamConversationDetail)
def team_say(key: str, conversation_id: int, data: TeamSayIn, ctx: OwnerAdminBrief) -> TeamConversationDetail:
    """Say something to the agent (with files attached before); returns the conversation with its answer."""
    # The answer takes several model calls: short transactions, none open while the agent thinks.
    def db() -> Any:
        return admin_transaction(write=True)

    with admin_transaction() as session:
        _conversation_of(session, key, conversation_id)
    try:
        said = agent_chat.answer(db, agent_key=key, conversation_id=conversation_id, text=data.text, attachment_ids=data.attachment_ids, admin_id=ctx.admin.id)
    except agent_chat.Unavailable as exc:
        raise ValidationFailed(str(exc), code="AGENT_NOT_READY") from exc
    with admin_transaction(write=True) as session:
        record(session, ctx.admin, "team.agent_asked", target_type="team_conversation", target_id=conversation_id, ip=ctx.ip,
               metadata={"agent": key, "tools": [c["tool"] for c in said.tool_calls], "proposals": said.proposals, "files": data.attachment_ids})
        return _conversation_detail(session, _conversation_of(session, key, conversation_id))
