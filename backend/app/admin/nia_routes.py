"""Admin → Nia (agent team, stage A2): talk to Nia, the latest brief, directives and memory.
Both admin roles can read; only owners talk to Nia, confirm directives and change memory."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core.errors import NotFound, ValidationFailed
from ..db import admin_transaction
from ..models import (
    MEMORY_KINDS,
    NiaConversation,
    NiaMeeting,
    NiaMemory,
    NiaMessage,
    OwnerDirective,
    TeamApproval,
    TeamRun,
)
from ..services.nia import chat, directives, meetings, memory, voice
from ..services.team import registry, tasks
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin, OwnerAdminBrief

router = APIRouter(prefix="/admin-api/nia", tags=["admin-nia"])


class NiaBriefOut(BaseModel):
    at: datetime
    status: str
    summary: str


class NiaStatusOut(BaseModel):
    ready: bool
    problem: str
    brief: NiaBriefOut | None
    proposed_directives: int
    pending_approvals: int


class NiaMessageOut(BaseModel):
    id: int
    role: str
    text: str
    tool_calls: list[dict[str, Any]]
    cost_usd: float
    created_at: datetime


class NiaConversationOut(BaseModel):
    id: int
    title: str
    updated_at: datetime


class NiaConversationDetail(NiaConversationOut):
    messages: list[NiaMessageOut]


class NiaSayIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class NiaDirectiveOut(BaseModel):
    id: int
    text: str
    agents: list[str]
    agent_names: list[str]
    goal: str
    due_on: date | None
    status: str
    reports: list[dict[str, Any]]
    created_at: datetime
    confirmed_at: datetime | None
    closed_at: datetime | None


class NiaSaidOut(BaseModel):
    conversation: NiaConversationDetail
    directives: list[NiaDirectiveOut]


class NiaDirectiveIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    agents: list[str] = Field(min_length=1)
    goal: str = Field(default="", max_length=2000)
    due_on: date | None = None


class NiaCloseIn(BaseModel):
    status: Literal["done", "cancelled"]


class NiaMemoryIn(BaseModel):
    kind: Literal["person", "decision", "preference", "work", "lesson", "fact"]
    text: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(default="", max_length=2000)
    topic: str = Field(default="", max_length=60)


class NiaMemoryOut(NiaMemoryIn):
    id: int
    source: str
    created_at: datetime
    updated_at: datetime


def _message(m: NiaMessage) -> NiaMessageOut:
    return NiaMessageOut(id=m.id, role=m.role, text=m.text, tool_calls=m.tool_calls, cost_usd=m.cost_usd, created_at=m.created_at)


def _conversation(session: AdminDb, c: NiaConversation) -> NiaConversationDetail:
    messages = session.execute(select(NiaMessage).where(NiaMessage.conversation_id == c.id).order_by(NiaMessage.id)).scalars()
    return NiaConversationDetail(id=c.id, title=c.title, updated_at=c.updated_at, messages=[_message(m) for m in messages])


def _directive(d: OwnerDirective) -> NiaDirectiveOut:
    names = [registry.AGENTS[a].name if a in registry.AGENTS else a for a in d.agents]
    return NiaDirectiveOut(id=d.id, text=d.text, agents=d.agents, agent_names=names, goal=d.goal, due_on=d.due_on, status=d.status, reports=d.reports,
                           created_at=d.created_at, confirmed_at=d.confirmed_at, closed_at=d.closed_at)


def _memory(m: NiaMemory) -> NiaMemoryOut:
    return NiaMemoryOut(id=m.id, kind=m.kind, topic=m.topic, text=m.text, evidence=m.evidence, source=m.source, created_at=m.created_at, updated_at=m.updated_at)


def _get(session: AdminDb, model: Any, key: int, what: str) -> Any:
    row = session.get(model, key)
    if row is None:
        raise NotFound(f"No such {what}.", code="NOT_FOUND")
    return row


@router.get("/status", response_model=NiaStatusOut)
def nia_status(_ctx: CurrentAdmin, session: AdminDb) -> NiaStatusOut:
    problem = chat.ready(session)
    last = session.execute(select(TeamRun).where(TeamRun.agent == "nia", TeamRun.status == "done").order_by(TeamRun.started_at.desc()).limit(1)).scalar_one_or_none()
    proposed = session.execute(select(func.count()).select_from(OwnerDirective).where(OwnerDirective.status == "proposed")).scalar() or 0
    pending = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.status == "pending")).scalar() or 0
    return NiaStatusOut(ready=not problem, problem=problem, brief=NiaBriefOut(at=last.started_at, status=last.status, summary=last.summary) if last else None,
                        proposed_directives=int(proposed), pending_approvals=int(pending))


@router.post("/brief", response_model=NiaStatusOut)
def nia_brief_now(ctx: OwnerAdmin, session: AdminDb) -> NiaStatusOut:
    problem = chat.ready(session)
    if problem:
        raise ValidationFailed(problem, code="NIA_NOT_READY")
    tasks.start(session, "nia", "manual")
    record(session, ctx.admin, "nia.brief_requested", target_type="nia", ip=ctx.ip)
    return nia_status(ctx, session)


@router.get("/conversations", response_model=list[NiaConversationOut])
def nia_conversations(_ctx: CurrentAdmin, session: AdminDb) -> list[NiaConversationOut]:
    rows = session.execute(select(NiaConversation).order_by(NiaConversation.updated_at.desc()).limit(100)).scalars()
    return [NiaConversationOut(id=c.id, title=c.title or "New conversation", updated_at=c.updated_at) for c in rows]


@router.post("/conversations", response_model=NiaConversationDetail, status_code=201)
def nia_new_conversation(ctx: OwnerAdmin, session: AdminDb) -> NiaConversationDetail:
    from datetime import UTC

    now = datetime.now(UTC)
    row = NiaConversation(title="", admin_id=ctx.admin.id, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return _conversation(session, row)


@router.get("/conversations/{conversation_id}", response_model=NiaConversationDetail)
def nia_conversation(conversation_id: int, _ctx: CurrentAdmin, session: AdminDb) -> NiaConversationDetail:
    return _conversation(session, _get(session, NiaConversation, conversation_id, "conversation"))


@router.delete("/conversations/{conversation_id}", status_code=204)
def nia_delete_conversation(conversation_id: int, ctx: OwnerAdmin, session: AdminDb) -> None:
    row = _get(session, NiaConversation, conversation_id, "conversation")
    record(session, ctx.admin, "nia.conversation_deleted", target_type="nia_conversation", target_id=row.id, ip=ctx.ip)
    session.delete(row)


@router.post("/conversations/{conversation_id}/messages", response_model=NiaSaidOut)
def nia_say(conversation_id: int, data: NiaSayIn, ctx: OwnerAdminBrief) -> NiaSaidOut:
    # Nia's answer takes several model calls: short transactions, none open while it thinks.
    def db() -> Any:
        return admin_transaction(write=True)

    try:
        said = chat.answer(db, conversation_id=conversation_id, text=data.text, admin_id=ctx.admin.id)
    except chat.NiaUnavailable as exc:
        raise ValidationFailed(str(exc), code="NIA_NOT_READY") from exc
    with admin_transaction(write=True) as session:
        record(session, ctx.admin, "nia.asked", target_type="nia_conversation", target_id=conversation_id, ip=ctx.ip,
               metadata={"tools": [c["tool"] for c in said.tool_calls], "directives": said.directives})
        conversation = _conversation(session, _get(session, NiaConversation, conversation_id, "conversation"))
        proposed = [_directive(d) for d in session.execute(select(OwnerDirective).where(OwnerDirective.id.in_(said.directives))).scalars()] if said.directives else []
    return NiaSaidOut(conversation=conversation, directives=proposed)


@router.get("/directives", response_model=list[NiaDirectiveOut])
def nia_directives(_ctx: CurrentAdmin, session: AdminDb, status: str = Query(default="open", max_length=10)) -> list[NiaDirectiveOut]:
    stmt = select(OwnerDirective).order_by(OwnerDirective.id.desc()).limit(200)
    if status == "open":
        stmt = stmt.where(OwnerDirective.status.in_(("proposed", "active")))
    elif status != "all":
        stmt = stmt.where(OwnerDirective.status == status)
    return [_directive(d) for d in session.execute(stmt).scalars()]


@router.post("/directives", response_model=NiaDirectiveOut, status_code=201)
def nia_add_directive(data: NiaDirectiveIn, ctx: OwnerAdmin, session: AdminDb) -> NiaDirectiveOut:
    """A directive written by hand (without Nia): confirmed at once, the owner wrote it."""
    try:
        row = directives.confirm(session, directives.propose(session, text=data.text, agents=data.agents, goal=data.goal, due_on=data.due_on))
    except directives.DirectiveRefused as exc:
        raise ValidationFailed(str(exc), code="DIRECTIVE_REFUSED") from exc
    record(session, ctx.admin, "nia.directive_confirmed", target_type="owner_directive", target_id=row.id, ip=ctx.ip, metadata={"agents": row.agents})
    return _directive(row)


@router.put("/directives/{directive_id}", response_model=NiaDirectiveOut)
def nia_edit_directive(directive_id: int, data: NiaDirectiveIn, ctx: OwnerAdmin, session: AdminDb) -> NiaDirectiveOut:
    row = _get(session, OwnerDirective, directive_id, "directive")
    if row.status != "proposed":
        raise ValidationFailed("Only a directive that is not confirmed yet can be changed.", code="DIRECTIVE_REFUSED")
    unknown = [a for a in data.agents if a not in registry.AGENTS]
    if unknown:
        raise ValidationFailed(f"No such agent: {', '.join(unknown)}", code="DIRECTIVE_REFUSED")
    row.text, row.agents, row.goal, row.due_on = data.text, sorted(set(data.agents)), data.goal, data.due_on
    return _directive(row)


@router.post("/directives/{directive_id}/confirm", response_model=NiaDirectiveOut)
def nia_confirm_directive(directive_id: int, ctx: OwnerAdmin, session: AdminDb) -> NiaDirectiveOut:
    row = _get(session, OwnerDirective, directive_id, "directive")
    try:
        directives.confirm(session, row)
    except directives.DirectiveRefused as exc:
        raise ValidationFailed(str(exc), code="DIRECTIVE_REFUSED") from exc
    record(session, ctx.admin, "nia.directive_confirmed", target_type="owner_directive", target_id=row.id, ip=ctx.ip, metadata={"agents": row.agents})
    return _directive(row)


@router.post("/directives/{directive_id}/close", response_model=NiaDirectiveOut)
def nia_close_directive(directive_id: int, data: NiaCloseIn, ctx: OwnerAdmin, session: AdminDb) -> NiaDirectiveOut:
    row = _get(session, OwnerDirective, directive_id, "directive")
    if row.status == "proposed" and data.status == "cancelled":
        row.status = "cancelled"
    else:
        try:
            directives.close(session, row, status=data.status)
        except directives.DirectiveRefused as exc:
            raise ValidationFailed(str(exc), code="DIRECTIVE_REFUSED") from exc
    record(session, ctx.admin, f"nia.directive_{data.status}", target_type="owner_directive", target_id=row.id, ip=ctx.ip)
    return _directive(row)


@router.get("/memories", response_model=list[NiaMemoryOut])
def nia_memories(_ctx: CurrentAdmin, session: AdminDb, kind: str = Query(default="", max_length=12), q: str = Query(default="", max_length=200),
                 topic: str = Query(default="", max_length=60)) -> list[NiaMemoryOut]:
    if q:
        rows = memory.search(session, q, limit=200)
    else:
        stmt = select(NiaMemory).where(NiaMemory.active.is_(True)).order_by(NiaMemory.updated_at.desc()).limit(500)
        rows = list(session.execute(stmt).scalars())
    return [_memory(m) for m in rows if (not kind or m.kind == kind) and (not topic or m.topic == topic)]


class NiaTopicOut(BaseModel):
    topic: str
    count: int
    kinds: dict[str, int]
    last: datetime


@router.get("/memory-topics", response_model=list[NiaTopicOut])
def nia_memory_topics(_ctx: CurrentAdmin, session: AdminDb) -> list[NiaTopicOut]:
    """The branches of Nia's memory: every topic with how many memories of each kind (the tree's first level)."""
    out: dict[str, NiaTopicOut] = {}
    for m in session.execute(select(NiaMemory).where(NiaMemory.active.is_(True))).scalars():
        t = out.setdefault(m.topic, NiaTopicOut(topic=m.topic, count=0, kinds={}, last=m.updated_at))
        t.count += 1
        t.kinds[m.kind] = t.kinds.get(m.kind, 0) + 1
        t.last = max(t.last, m.updated_at)
    return sorted(out.values(), key=lambda t: (-t.count, t.topic))


class NiaOwnerIn(BaseModel):
    name: str = Field(default="", max_length=200)
    address_as: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=300)
    about: str = Field(default="", max_length=10000)


@router.get("/owner", response_model=NiaOwnerIn)
def nia_owner(_ctx: CurrentAdmin, session: AdminDb) -> NiaOwnerIn:
    """Who Nia talks with and how to address them (the owner writes it; Nia never guesses a name)."""
    return NiaOwnerIn(**memory.owner(session))


@router.put("/owner", response_model=NiaOwnerIn)
def put_nia_owner(data: NiaOwnerIn, ctx: OwnerAdmin, session: AdminDb) -> NiaOwnerIn:
    saved = memory.save_owner(session, data.model_dump())
    record(session, ctx.admin, "nia.owner_profile_changed", target_type="nia", ip=ctx.ip)
    return NiaOwnerIn(**saved)


@router.post("/memories", response_model=NiaMemoryOut, status_code=201)
def nia_add_memory(data: NiaMemoryIn, ctx: OwnerAdmin, session: AdminDb) -> NiaMemoryOut:
    row = memory.add(session, kind=data.kind, text=data.text, evidence=data.evidence, topic=data.topic, source=f"owner ({ctx.admin.email})")
    record(session, ctx.admin, "nia.memory_added", target_type="nia_memory", target_id=row.id, ip=ctx.ip)
    return _memory(row)


@router.put("/memories/{memory_id}", response_model=NiaMemoryOut)
def nia_change_memory(memory_id: int, data: NiaMemoryIn, ctx: OwnerAdmin, session: AdminDb) -> NiaMemoryOut:
    from datetime import UTC

    row = _get(session, NiaMemory, memory_id, "memory")
    if data.kind not in MEMORY_KINDS:
        raise ValidationFailed("Unknown kind.", code="MEMORY_KIND")
    row.kind, row.text, row.evidence, row.topic, row.updated_at = data.kind, data.text, data.evidence, memory.clean_topic(data.topic), datetime.now(UTC)
    record(session, ctx.admin, "nia.memory_changed", target_type="nia_memory", target_id=row.id, ip=ctx.ip)
    return _memory(row)


@router.delete("/memories/{memory_id}", status_code=204)
def nia_forget(memory_id: int, ctx: OwnerAdmin, session: AdminDb) -> None:
    row = _get(session, NiaMemory, memory_id, "memory")
    record(session, ctx.admin, "nia.memory_deleted", target_type="nia_memory", target_id=row.id, ip=ctx.ip, metadata={"kind": row.kind})
    session.delete(row)


# --- voice -------------------------------------------------------------------------------------------------
class NiaVoiceSettingsOut(voice.VoiceSettings):
    voices: list[str]
    problem: str
    minutes_today: float


class NiaVoiceLeaseIn(BaseModel):
    conversation_id: int


class NiaVoiceLeaseOut(BaseModel):
    token: str
    path: str


@router.get("/voice", response_model=NiaVoiceSettingsOut)
def nia_voice_settings(_ctx: CurrentAdmin, session: AdminDb) -> NiaVoiceSettingsOut:
    value = voice.settings_of(session)
    return NiaVoiceSettingsOut(**value.model_dump(), voices=list(voice.VOICES), problem=voice.problem(session), minutes_today=round(voice.minutes_today(session), 1))


@router.put("/voice", response_model=NiaVoiceSettingsOut)
def put_nia_voice(data: voice.VoiceSettings, ctx: OwnerAdmin, session: AdminDb) -> NiaVoiceSettingsOut:
    try:
        voice.save(session, data)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="NIA_VOICE") from exc
    record(session, ctx.admin, "nia.voice_changed", target_type="nia", ip=ctx.ip, metadata=data.model_dump())
    return nia_voice_settings(ctx, session)


@router.post("/voice/lease", response_model=NiaVoiceLeaseOut)
def nia_voice_lease(data: NiaVoiceLeaseIn, ctx: OwnerAdmin, session: AdminDb) -> NiaVoiceLeaseOut:
    """A one-time ticket for the voice connection, valid for a minute (the WebSocket carries no
    CSRF header, so the authenticated request that makes the ticket is the check)."""
    reason = voice.problem(session)
    if reason:
        raise ValidationFailed(reason, code="NIA_VOICE_NOT_READY")
    _get(session, NiaConversation, data.conversation_id, "conversation")
    record(session, ctx.admin, "nia.voice_started", target_type="nia_conversation", target_id=data.conversation_id, ip=ctx.ip)
    return NiaVoiceLeaseOut(token=voice.lease(ctx.admin.id, data.conversation_id), path="/admin-api/nia/voice/live")


@router.websocket("/voice/live")
async def nia_voice_live(socket: WebSocket, lease: str = "") -> None:
    from ..config import settings
    from ..core.net import client_ip
    from ..main import _allowed

    cfg = settings()
    origin = socket.headers.get("origin", "")
    expected = (cfg.admin_public_base_url or "").rstrip("/")
    if not _allowed(client_ip(socket), cfg.csv(cfg.admin_ip_allowlist)) or (expected and origin != expected):
        await socket.close(code=4403)
        return
    held = voice.take(lease)
    if held is None:
        await socket.close(code=4401)
        return
    await socket.accept()

    def db() -> Any:
        return admin_transaction(write=True)

    await voice.relay(socket, held, db)


# --- meetings and presentations ---------------------------------------------------------------------------
class NiaMeetingIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    minutes: int = Field(ge=5, le=240)
    agenda: str = Field(default="", max_length=5000)
    prepare_hours: int = Field(ge=0, le=168)
    repeat_weekly: bool


class NiaMeetingOut(NiaMeetingIn):
    id: int
    status: str
    slides: int
    prepared_at: datetime | None
    conversation_id: int | None
    minutes_text: str


class NiaMeetingDetail(NiaMeetingOut):
    deck: list[dict[str, Any]]


def _meeting(m: NiaMeeting) -> NiaMeetingOut:
    return NiaMeetingOut(id=m.id, title=m.title, starts_at=m.starts_at, minutes=m.minutes, agenda=m.agenda, prepare_hours=m.prepare_hours, repeat_weekly=m.repeat_weekly,
                         status=m.status, slides=len(m.deck), prepared_at=m.prepared_at, conversation_id=m.conversation_id, minutes_text=m.minutes_text)


@router.get("/meetings", response_model=list[NiaMeetingOut])
def nia_meetings(_ctx: CurrentAdmin, session: AdminDb) -> list[NiaMeetingOut]:
    rows = session.execute(select(NiaMeeting).where(NiaMeeting.status != "cancelled").order_by(NiaMeeting.starts_at.desc()).limit(100)).scalars()
    return [_meeting(m) for m in rows]


@router.post("/meetings", response_model=NiaMeetingOut, status_code=201)
def nia_add_meeting(data: NiaMeetingIn, ctx: OwnerAdmin, session: AdminDb) -> NiaMeetingOut:
    from datetime import UTC

    now = datetime.now(UTC)
    if data.starts_at.tzinfo is None or data.starts_at <= now:
        raise ValidationFailed("Choose a time in the future (with its time zone).", code="MEETING_TIME")
    row = NiaMeeting(**data.model_dump(), status="scheduled", deck=[], created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    record(session, ctx.admin, "nia.meeting_scheduled", target_type="nia_meeting", target_id=row.id, ip=ctx.ip, metadata={"starts_at": data.starts_at.isoformat()})
    return _meeting(row)


@router.get("/meetings/{meeting_id}", response_model=NiaMeetingDetail)
def nia_meeting(meeting_id: int, _ctx: CurrentAdmin, session: AdminDb) -> NiaMeetingDetail:
    row = _get(session, NiaMeeting, meeting_id, "meeting")
    return NiaMeetingDetail(**_meeting(row).model_dump(), deck=row.deck)


@router.put("/meetings/{meeting_id}", response_model=NiaMeetingOut)
def nia_change_meeting(meeting_id: int, data: NiaMeetingIn, ctx: OwnerAdmin, session: AdminDb) -> NiaMeetingOut:
    from datetime import UTC

    row = _get(session, NiaMeeting, meeting_id, "meeting")
    if row.status in ("held", "cancelled"):
        raise ValidationFailed("This meeting is over.", code="MEETING_OVER")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    record(session, ctx.admin, "nia.meeting_changed", target_type="nia_meeting", target_id=row.id, ip=ctx.ip)
    return _meeting(row)


@router.delete("/meetings/{meeting_id}", response_model=NiaMeetingOut)
def nia_cancel_meeting(meeting_id: int, ctx: OwnerAdmin, session: AdminDb) -> NiaMeetingOut:
    row = _get(session, NiaMeeting, meeting_id, "meeting")
    row.status = "cancelled"
    record(session, ctx.admin, "nia.meeting_cancelled", target_type="nia_meeting", target_id=row.id, ip=ctx.ip)
    return _meeting(row)


@router.post("/meetings/{meeting_id}/prepare", response_model=NiaMeetingOut)
def nia_prepare_meeting(meeting_id: int, ctx: OwnerAdmin, session: AdminDb) -> NiaMeetingOut:
    from ..services.team import tasks as team_tasks  # noqa: F401 - the task types
    from ..workers import queue as q

    row = _get(session, NiaMeeting, meeting_id, "meeting")
    problem = chat.ready(session)
    if problem:
        raise ValidationFailed(problem, code="NIA_NOT_READY")
    q.enqueue(session, "nia.prepare", {"meeting_id": row.id}, queue="default", max_attempts=1, dedupe_key=f"nia.prepare:{row.id}")
    record(session, ctx.admin, "nia.meeting_prepare", target_type="nia_meeting", target_id=row.id, ip=ctx.ip)
    return _meeting(row)


@router.get("/meetings/{meeting_id}/pdf")
def nia_meeting_pdf(meeting_id: int, _ctx: CurrentAdmin, session: AdminDb) -> Any:
    from fastapi.responses import Response

    row = _get(session, NiaMeeting, meeting_id, "meeting")
    return Response(meetings.deck_pdf(row), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="nia-meeting-{row.id}.pdf"'})


@router.post("/meetings/{meeting_id}/present", response_model=NiaVoiceLeaseOut)
def nia_present(meeting_id: int, ctx: OwnerAdmin, session: AdminDb) -> NiaVoiceLeaseOut:
    row = _get(session, NiaMeeting, meeting_id, "meeting")
    if not row.deck:
        raise ValidationFailed("The deck is not ready yet. Let Nia prepare it first.", code="DECK_NOT_READY")
    reason = voice.problem(session)
    if reason:
        raise ValidationFailed(reason, code="NIA_VOICE_NOT_READY")
    conversation_id = meetings.conversation_for(session, row, ctx.admin.id)
    record(session, ctx.admin, "nia.meeting_presented", target_type="nia_meeting", target_id=row.id, ip=ctx.ip)
    return NiaVoiceLeaseOut(token=voice.lease(ctx.admin.id, conversation_id, row.id), path="/admin-api/nia/voice/live")
