"""Meetings with Nia (§5.0 "Meetings and presentations", stage A2c).

The owner schedules a meeting (time, length, agenda, how many hours before Nia prepares,
weekly or once) — there is no default meeting. At the preparation time Nia builds the deck with
its tools: every slide's data is frozen when it is made (numbers, series, funnels, lists), with
a talk track and, where showing the real page is clearer, the admin page to open and the element
to point at. Nia presents by voice (services/nia/voice.py); afterwards it writes the minutes from
what was said. The deck can be downloaded as a PDF.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import NiaConversation, NiaMeeting, NiaMessage
from ..insights import queries
from ..team import approvals, config, registry, runner, tools

# The admin pages Nia may open while presenting, and what can be pointed at on them
# (the elements carry data-nia="…" in the admin app).
PAGES: dict[str, str] = {
    "/insights": "Today: the main numbers (point at kpi:<key>)",
    "/insights/agents": "The agents' work over time",
    "/insights/money": "AI cost",
    "/insights/performance": "Speed and errors",
    "/team": "The agents (point at agent:<key>)",
    "/clearance": "What waits for the owner's clearance (point at approval:<id>)",
    "/marketing/calendar": "Posts to publish",
    "/nia/memory": "What Nia remembers",
}
SLIDE_KINDS = ("title", "text", "metric", "approvals", "agents", "page")
MAX_SLIDES = 30


def _freeze(session: Session, kind: str, args: dict[str, Any]) -> dict[str, Any]:
    if kind == "metric":
        spec = queries.BY_KEY.get(str(args.get("metric") or ""))
        if spec is None:
            raise ValueError(f"Unknown metric. Choose from: {', '.join(queries.BY_KEY)}")
        days = max(7, min(int(args.get("days") or 30), 365))
        k = queries.kpi(session, spec, days)
        return {"metric": spec.key, "label": spec.label, "unit": spec.unit, "good": spec.good, "days": days, "value": k["value"], "previous": k["previous"],
                "series": k["spark"], "start": queries.period(days)[0].isoformat()}
    if kind in ("approvals", "agents"):
        name = {"approvals": "approvals_waiting", "agents": "team_overview"}[kind]
        reader = tools.CATALOGUE[name][0]
        ctx = tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_single(session), granted=(name,))
        return reader(ctx)
    if kind == "page":
        path = str(args.get("path") or "")
        if path not in PAGES:
            raise ValueError(f"Nia can open: {', '.join(PAGES)}")
        return {"path": path, "query": str(args.get("query") or "")[:200], "highlight": str(args.get("highlight") or "")[:80]}
    return {}


def _single(session: Session) -> tools.DbFactory:
    """Run a reader inside the caller's session (no second transaction)."""
    from contextlib import contextmanager

    @contextmanager
    def use() -> Any:
        yield session

    return use


def add_slide(ctx: tools.Context, kind: str, title: str, talk: str, bullets: list[str] | None = None, metric: str = "", days: int = 30, funnel: str = "",
              weeks: int = 8, path: str = "", query: str = "", highlight: str = "") -> dict[str, Any]:
    if ctx.meeting_id is None:
        return {"error": "There is no meeting being prepared."}
    if kind not in SLIDE_KINDS:
        return {"error": f"kind is one of {', '.join(SLIDE_KINDS)}"}
    with ctx.db() as session:
        meeting = session.get(NiaMeeting, ctx.meeting_id)
        if meeting is None:
            return {"error": "The meeting is gone."}
        if len(meeting.deck) >= MAX_SLIDES:
            return {"error": f"The deck has {MAX_SLIDES} slides already."}
        try:
            data = _freeze(session, kind, {"metric": metric, "days": days, "funnel": funnel, "weeks": weeks, "path": path, "query": query, "highlight": highlight})
        except ValueError as exc:
            return {"error": str(exc)}
        slide = {"kind": kind, "title": title.strip()[:200], "talk": talk.strip()[:3000], "bullets": [str(b)[:300] for b in (bullets or [])][:8], "data": data,
                 "made_at": datetime.now(UTC).isoformat()}
        meeting.deck = [*meeting.deck, slide]
        number = len(meeting.deck)
    return {"slide": number}


tools.CATALOGUE["add_slide"] = (
    add_slide,
    "Add the next slide to the meeting's deck. kind: title, text (bullets), metric (a KPI key and days: chart of the period), "
    "approvals, agents, page (an admin page to open live: path, query like 'status=pending', highlight like 'kpi:cleared'). talk: what you will say.",
    {"kind": {"type": "STRING"}, "title": {"type": "STRING"}, "talk": {"type": "STRING"}, "bullets": {"type": "ARRAY", "items": {"type": "STRING"}},
     "metric": {"type": "STRING"}, "days": {"type": "INTEGER"}, "funnel": {"type": "STRING"}, "weeks": {"type": "INTEGER"},
     "path": {"type": "STRING"}, "query": {"type": "STRING"}, "highlight": {"type": "STRING"}},
    ["kind", "title", "talk"],
)


def _prep_prompt(meeting: NiaMeeting, previous: NiaMeeting | None) -> str:
    since = previous.starts_at.isoformat() if previous else "the beginning"
    open_points = previous.minutes_text[:3000] if previous and previous.minutes_text else "none"
    return (f"Prepare the deck for the meeting '{meeting.title}' on {meeting.starts_at.isoformat()} ({meeting.minutes} minutes).\n"
            f"The owner's agenda: {meeting.agenda or '(none given: use the standing order)'}\n"
            f"Since the last meeting ({since}). Minutes of the last meeting (start with what was promised):\n{open_points}\n\n"
            "Look at the numbers and the team first, then add slides in order with add_slide: a title slide; where the company stands (the KPIs that "
            "matter, as metric slides); what changed and why; each department in brief; what needs the owner (proposals waiting for clearance, settings agents wait for, "
            f"decisions); and the follow-ups. Size it to {meeting.minutes} minutes (about one slide per minute or two). Use page slides when showing the "
            f"live page is clearer. Pages you may open: {', '.join(PAGES)}. KPI keys: {', '.join(queries.BY_KEY)}. Every number from your tools.")


Prepare = Callable[[tools.DbFactory, int], dict[str, Any]]


def prepare(db: tools.DbFactory, meeting_id: int) -> dict[str, Any]:
    nia = registry.AGENTS["nia"]
    with db() as session:
        meeting = session.get(NiaMeeting, meeting_id)
        if meeting is None or meeting.status in ("held", "cancelled"):
            return {"skipped": "no meeting to prepare"}
        status, waiting = config.status(session, "nia")
        if status != "ready":
            return {"skipped": f"Nia is {status}" + (f": {', '.join(waiting)}" if waiting else "")}
        problem = runner.budget_problem(session, "nia")
        if problem:
            return {"skipped": problem}
        previous = session.execute(select(NiaMeeting).where(NiaMeeting.starts_at < meeting.starts_at, NiaMeeting.status == "held")
                                   .order_by(NiaMeeting.starts_at.desc()).limit(1)).scalar_one_or_none()
        mine, team = config.agent(session, "nia"), config.team(session)
        playbook_row = approvals.latest_playbook(session, "nia")
        # Read while the session is open: the row is detached once it closes.
        playbook = playbook_row.text if playbook_row else ""
        prompt = _prep_prompt(meeting, previous)
        meeting.status, meeting.deck = "preparing", []
    granted = (*tools.usable(nia, mine.tools), "add_slide")
    ctx = tools.Context(agent=nia, run_id=0, db=db, granted=granted, meeting_id=meeting_id)

    def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return tools.run(ctx, name, args)

    def before_round() -> None:
        with db() as session:
            reason = runner.budget_problem(session, "nia")
        if reason:
            raise runner.BudgetStop(reason)

    def on_usage(u: runner.Usage) -> None:
        from ...ai.client import record_usage

        with db() as session:
            record_usage(model=u.model, operation="agent.nia.meeting", agent_type="team", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                         latency_ms=u.latency_ms, success=True, session=session)

    system = runner.system_prompt(nia, mine, team, playbook)
    try:
        runner.MODEL.converse(tier=mine.tier or "fast", system=system, prompt=prompt, declarations=tools.declarations(granted), call_tool=call_tool,
                              before_round=before_round, on_usage=on_usage)
        outcome = "prepared"
    except runner.BudgetStop as exc:
        outcome = f"stopped: {exc}"
    except Exception as exc:  # noqa: BLE001 - recorded on the meeting, retried at the next tick
        outcome = f"failed: {exc.__class__.__name__}"
    with db() as session:
        meeting = session.get(NiaMeeting, meeting_id)
        if meeting is None:
            return {"skipped": "gone"}
        meeting.status = "prepared" if meeting.deck else "scheduled"
        meeting.deck_version += 1 if meeting.deck else 0
        meeting.prepared_at = datetime.now(UTC) if meeting.deck else None
        meeting.updated_at = datetime.now(UTC)
        return {"meeting": meeting_id, "slides": len(meeting.deck), "outcome": outcome}


def tick(db: tools.DbFactory, now: datetime | None = None, *, prepare_fn: Prepare = prepare) -> dict[str, Any]:
    """Every few minutes: prepare what is due, close what is over, schedule the next weekly one."""
    now = now or datetime.now(UTC)
    due: list[int] = []
    held: list[int] = []
    with db() as session:
        for meeting in session.execute(select(NiaMeeting).where(NiaMeeting.status.in_(("scheduled", "prepared", "preparing")))).scalars():
            end = meeting.starts_at + timedelta(minutes=meeting.minutes)
            if end <= now:
                meeting.status, meeting.updated_at = "held", now
                held.append(meeting.id)
                if meeting.repeat_weekly:
                    later = meeting.starts_at + timedelta(days=7)
                    while later + timedelta(minutes=meeting.minutes) <= now:
                        later += timedelta(days=7)
                    if session.execute(select(NiaMeeting.id).where(NiaMeeting.starts_at == later, NiaMeeting.title == meeting.title)).first() is None:
                        session.add(NiaMeeting(title=meeting.title, starts_at=later, minutes=meeting.minutes, agenda=meeting.agenda, prepare_hours=meeting.prepare_hours,
                                               repeat_weekly=True, status="scheduled", deck=[], created_at=now, updated_at=now))
            elif meeting.status == "scheduled" and meeting.starts_at - timedelta(hours=meeting.prepare_hours) <= now:
                due.append(meeting.id)
    prepared = [prepare_fn(db, meeting_id) for meeting_id in due]
    written = [write_minutes(db, meeting_id) for meeting_id in held]
    return {"prepared": prepared, "held": held, "minutes": written}


def write_minutes(db: tools.DbFactory, meeting_id: int) -> dict[str, Any]:
    """After the meeting: minutes from what was said (the meeting's conversation)."""
    with db() as session:
        meeting = session.get(NiaMeeting, meeting_id)
        if meeting is None or meeting.conversation_id is None or meeting.minutes_text:
            return {"meeting": meeting_id, "minutes": "nothing said"}
        said = session.execute(select(NiaMessage).where(NiaMessage.conversation_id == meeting.conversation_id).order_by(NiaMessage.id)).scalars()
        transcript = "\n".join(f"{'OWNER' if m.role == 'owner' else 'NIA'}: {m.text}" for m in said)[-40000:]
        if not transcript or runner.budget_problem(session, "nia") or config.status(session, "nia")[0] != "ready":
            return {"meeting": meeting_id, "minutes": "skipped"}
        mine = config.agent(session, "nia")
    started = time.perf_counter()
    try:
        text = runner.MODEL.converse(tier=mine.tier or "fast", system="You write meeting minutes for the owner of the company. Plain text, short.",
                                     prompt=(f"Meeting '{meeting.title}' ({meeting.starts_at:%d.%m.%Y}). Write the minutes from this transcript: what was shown, what "
                                             f"was decided, directives given, open questions and follow-ups for the next meeting.\n\n{transcript}"),
                                     declarations=[], call_tool=lambda _n, _a: {}, before_round=lambda: None, on_usage=lambda _u: None)
    except Exception as exc:  # noqa: BLE001 - tried again at the next tick only if empty
        return {"meeting": meeting_id, "minutes": f"failed: {exc.__class__.__name__}"}
    with db() as session:
        row = session.get(NiaMeeting, meeting_id)
        if row is not None:
            row.minutes_text, row.updated_at = (text or "").strip()[:20000], datetime.now(UTC)
    return {"meeting": meeting_id, "minutes": "written", "ms": int((time.perf_counter() - started) * 1000)}


def conversation_for(session: Session, meeting: NiaMeeting, admin_id: int) -> int:
    if meeting.conversation_id is None:
        now = datetime.now(UTC)
        row = NiaConversation(title=f"Meeting: {meeting.title}"[:200], admin_id=admin_id, created_at=now, updated_at=now)
        session.add(row)
        session.flush()
        meeting.conversation_id = row.id
    return meeting.conversation_id


def deck_pdf(meeting: NiaMeeting) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

    from ..pdf_fonts import BOLD, REGULAR

    title = ParagraphStyle("t", fontName=BOLD, fontSize=22, leading=27, spaceAfter=10)
    body = ParagraphStyle("b", fontName=REGULAR, fontSize=12, leading=16, spaceAfter=6)
    small = ParagraphStyle("s", fontName=REGULAR, fontSize=9, leading=12, textColor="#5b6573")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=15 * mm, title=meeting.title)
    story: list[Any] = []
    for n, slide in enumerate(meeting.deck, start=1):
        if n > 1:
            story.append(PageBreak())
        story.append(Paragraph(escape(slide.get("title") or ""), title))
        data = slide.get("data") or {}
        if slide.get("kind") == "metric":
            story.append(Paragraph(f"{escape(str(data.get('label')))}: <b>{data.get('value')}</b> (before: {data.get('previous')}) · last {data.get('days')} days", body))
        elif slide.get("kind") == "funnel":
            story.append(Table([[escape(str(s.get("step"))), str(s.get("value"))] for s in data.get("steps", [])]))
        elif slide.get("kind") == "page":
            story.append(Paragraph(f"Live page: {escape(str(data.get('path')))}{'?' + escape(str(data.get('query'))) if data.get('query') else ''}", body))
        for bullet in slide.get("bullets") or []:
            story.append(Paragraph(f"• {escape(str(bullet))}", body))
        if slide.get("talk"):
            story += [Spacer(1, 6 * mm), Paragraph(escape(str(slide["talk"])), small)]
    if meeting.minutes_text:
        story += [PageBreak(), Paragraph("Minutes", title), *[Paragraph(escape(line), body) for line in meeting.minutes_text.splitlines() if line.strip()]]
    if not story:
        story.append(Paragraph(escape(meeting.title or "Meeting"), title))
    doc.build(story)
    return buffer.getvalue()
