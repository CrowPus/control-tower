"""Admin → Nia → Browser: the owner starts Nia in the browser, watches the screen live,
answers when Nia asks, takes over (clicks and types in the same browser) and hands back, pauses or stops; and sets the
rules. Owner only: the screen may show anything the owner is signed in to. Rule changes and starts are in the admin
log; what the owner types is never written down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import Conflict, NotFound, ValidationFailed
from ..models import BrowserSession, BrowserStep
from ..services.nia import browser
from .auth import record
from .deps import AdminDb, OwnerAdmin

router = APIRouter(prefix="/admin-api/nia/browser", tags=["admin-nia-browser"])


class BrowserRules(BaseModel):
    enabled: bool = False
    max_minutes: int | None = Field(default=None, ge=1, le=240)
    max_turns: int | None = Field(default=None, ge=1, le=500)
    blocked_sites: list[str] = Field(default_factory=list, max_length=100)
    keep_signed_in: list[str] = Field(default_factory=list, max_length=100)


class StepOut(BaseModel):
    id: int
    kind: str
    text: str
    url: str
    at: str
    has_screenshot: bool


class SessionOut(BaseModel):
    id: int
    place: str = "cloud"
    goal: str
    status: str
    reason: str
    url: str
    turns: int
    result: str
    cost_usd: float
    question: str
    asks_in_words: bool
    created_at: str
    ended_at: str | None
    steps: list[StepOut]


class SessionRow(BaseModel):
    id: int
    goal: str
    status: str
    result: str
    cost_usd: float
    created_at: datetime


class BrowserOut(BaseModel):
    rules: BrowserRules
    active: SessionOut | None
    recent: list[SessionRow]
    spent_today_usd: float


def _rules(session: AdminDb) -> BrowserRules:
    r = browser.rules(session)
    return BrowserRules(enabled=r.enabled, max_minutes=r.max_minutes, max_turns=r.max_turns,
                        blocked_sites=list(r.blocked_sites), keep_signed_in=list(r.keep_signed_in))


def _session(session: AdminDb, session_id: int) -> BrowserSession:
    row = session.get(BrowserSession, session_id)
    if row is None:
        raise NotFound("No such browsing session.")
    return row


@router.get("", response_model=BrowserOut)
def browser_overview(_ctx: OwnerAdmin, session: AdminDb) -> BrowserOut:
    row = browser.active(session)
    recent = session.execute(select(BrowserSession).order_by(BrowserSession.id.desc()).limit(15)).scalars()
    return BrowserOut(rules=_rules(session), active=SessionOut.model_validate(browser.view(session, row)) if row else None,
                      recent=[SessionRow(id=r.id, goal=r.goal, status=r.status, result=r.result, cost_usd=round(float(r.cost_usd or 0), 4), created_at=r.created_at) for r in recent],
                      spent_today_usd=round(browser.spent_today(session), 4))


@router.put("/rules", response_model=BrowserRules)
def put_browser_rules(data: BrowserRules, ctx: OwnerAdmin, session: AdminDb) -> BrowserRules:
    before = _rules(session).model_dump()
    browser.save_rules(session, data.model_dump())
    after = _rules(session)
    record(session, ctx.admin, "nia.browser_rules_changed", target_type="nia", ip=ctx.ip, metadata={"before": before, "after": after.model_dump()})
    return after


class StartIn(BaseModel):
    goal: str = Field(min_length=5, max_length=4000)
    url: str = Field(default="", max_length=1000)


@router.post("/sessions", response_model=SessionOut, status_code=201)
def start_browsing(data: StartIn, ctx: OwnerAdmin, session: AdminDb) -> SessionOut:
    try:
        row = browser.start(session, admin_id=ctx.admin.id, goal=data.goal, url=data.url)
    except browser.Refused as exc:
        raise Conflict(str(exc), code="BROWSER_REFUSED") from exc
    record(session, ctx.admin, "nia.browser_started", target_type="browser_session", target_id=row.id, ip=ctx.ip, metadata={"goal": data.goal[:300], "url": data.url})
    return SessionOut.model_validate(browser.view(session, row))


@router.get("/sessions/{session_id}", response_model=SessionOut)
def browsing_session(session_id: int, _ctx: OwnerAdmin, session: AdminDb) -> SessionOut:
    row = _session(session, session_id)
    if row.status in ("running", "paused", "asking", "owner", "idle"):
        browser.watched(session, row)
    return SessionOut.model_validate(browser.view(session, row))


@router.get("/sessions/{session_id}/frame", response_class=Response)
def browsing_frame(session_id: int, _ctx: OwnerAdmin, session: AdminDb) -> Response:
    """The browser's screen now (JPEG). Asking for it is what "the owner is watching" means."""
    row = _session(session, session_id)
    if row.status not in ("running", "paused", "asking", "owner", "idle"):
        raise Conflict("This session is over.", code="BROWSER_OVER")
    # The screen first, then this request's own writes: nothing of this request is held while the computer answers.
    try:
        data, url = browser.client_for(row).frame(row.key)
    except browser.BrowserError as exc:
        raise Conflict("The browser is not reachable.", code="BROWSER_UNREACHABLE") from exc
    browser.watched(session, row)
    if url and url != row.url and datetime.now(UTC) - row.updated_at > timedelta(seconds=2):
        row.url = url[:1000]
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class AnswerIn(BaseModel):
    decision: Literal["allow", "take_over", "hand_back", "pause", "resume", "stop"]
    note: str = Field(default="", max_length=1000)


@router.post("/sessions/{session_id}/answer", response_model=SessionOut)
def answer_browsing(session_id: int, data: AnswerIn, ctx: OwnerAdmin, session: AdminDb) -> SessionOut:
    row = _session(session, session_id)
    try:
        browser.answer(session, row, data.decision, note=data.note)
    except browser.Refused as exc:
        raise Conflict(str(exc), code="BROWSER_REFUSED") from exc
    record(session, ctx.admin, f"nia.browser_{data.decision}", target_type="browser_session", target_id=row.id, ip=ctx.ip)
    return SessionOut.model_validate(browser.view(session, row))


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/sessions/{session_id}/reply", response_model=SessionOut)
def reply_browsing(session_id: int, data: ReplyIn, _ctx: OwnerAdmin, session: AdminDb) -> SessionOut:
    row = _session(session, session_id)
    try:
        browser.reply(session, row, data.text)
    except browser.Refused as exc:
        raise Conflict(str(exc), code="BROWSER_REFUSED") from exc
    return SessionOut.model_validate(browser.view(session, row))


class ActIn(BaseModel):
    action: Literal["click", "double_click", "type", "key", "scroll", "back", "forward", "navigate"]
    x: int | None = Field(default=None, ge=0, le=browser.WIDTH)
    y: int | None = Field(default=None, ge=0, le=browser.HEIGHT)
    text: str = Field(default="", max_length=2000)
    keys: str = Field(default="", max_length=40)
    url: str = Field(default="", max_length=1000)
    direction: Literal["up", "down", "left", "right"] = "down"


@router.post("/sessions/{session_id}/act", response_model=dict[str, Any])
def owner_acts(session_id: int, data: ActIn, _ctx: OwnerAdmin, session: AdminDb) -> dict[str, Any]:
    """The owner's own click, typing or key in Nia's browser (the owner has the browser while doing it)."""
    row = _session(session, session_id)
    if data.action == "navigate" and not data.url.startswith(("http://", "https://")):
        raise ValidationFailed("Give a web address (https://…).", code="BAD_URL")
    try:
        return browser.owner_action(session, row, data.model_dump())
    except browser.Refused as exc:
        raise Conflict(str(exc), code="BROWSER_REFUSED") from exc
    except browser.BrowserError as exc:
        raise Conflict("The browser is not reachable.", code="BROWSER_UNREACHABLE") from exc


@router.get("/steps/{step_id}/screenshot", response_class=Response)
def step_screenshot(step_id: int, _ctx: OwnerAdmin, session: AdminDb) -> Response:
    """The screen at the moment Nia asked (kept with the session's record)."""
    step = session.get(BrowserStep, step_id)
    if step is None or step.screenshot is None:
        raise NotFound("No screenshot.")
    media = "image/png" if step.screenshot[:4] == b"\x89PNG" else "image/jpeg"
    return Response(step.screenshot, media_type=media, headers={"Cache-Control": "no-store"})
