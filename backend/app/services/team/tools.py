"""The agents' tools: small typed functions with their own
checks. An agent gets a tool only when its design lists it and the owner granted it; unknown
tools and unexpected arguments are refused, never guessed.

Tool results are data for the model. Every tool opens its own short transaction, so no
database transaction stays open while the model thinks.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...core.logging import get_logger
from ...models import (
    OwnerDirective,
    TeamApproval,
    TeamEvent,
    TeamRun,
)
from ..brain import graph as brain
from ..insights import queries
from ..nia import directives as owner_directives
from ..nia import memory as nia_memory
from . import (
    approvals,
    events,
    registry,
    web,
)

log = get_logger(__name__)

DbFactory = Callable[[], AbstractContextManager[Session]]

# Safety rails for one run, not business values: how much a tool hands back to the model.
NOTES_KEPT = 40
EVENTS_SHOWN = 30
BRAIN_FOUND = 12


@dataclass
class Context:
    agent: registry.Agent
    run_id: int
    db: DbFactory
    granted: tuple[str, ...]
    proposals: list[int] = field(default_factory=list)
    emitted: list[int] = field(default_factory=list)
    conversation_id: int | None = None
    directives: list[int] = field(default_factory=list)
    # The signed-in owner Nia is talking to (chat or voice): only then may Nia record a decision.
    admin_id: int | None = None
    # The meeting whose deck Nia is preparing (services/nia/meetings.py).
    meeting_id: int | None = None
    # The scout the Scout agent works for in this run (my_focus), its web searches and pages handed to the engine.
    scout: Any = None
    searches: int = 0
    submitted: list[str] = field(default_factory=list)


def _read_insights(ctx: Context, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    with ctx.db() as session:
        kpis = []
        for spec in queries.KPIS:
            k = queries.kpi(session, spec, days)
            kpis.append({"key": spec.key, "label": spec.label, "unit": spec.unit, "value": k["value"], "previous_period": k["previous"]})
    return {"days": days, "kpis": kpis}


def _read_events(ctx: Context, type: str = "", limit: int = 20) -> dict[str, Any]:  # noqa: A002 - the model's word
    limit = max(1, min(int(limit), EVENTS_SHOWN))
    with ctx.db() as session:
        stmt = select(TeamEvent).order_by(TeamEvent.id.desc()).limit(limit)
        if type:
            stmt = stmt.where(TeamEvent.type == type)
        rows = list(session.execute(stmt).scalars())
        return {"events": [{"id": e.id, "type": e.type, "source": e.source, "at": e.created_at.isoformat(), "payload": e.payload} for e in rows]}


def _keep_note(ctx: Context, text: str, evidence: str = "") -> dict[str, Any]:
    text = text.strip()[:600]
    if not text:
        return {"error": "The note is empty."}
    with ctx.db() as session:
        state = events.state(session, ctx.agent.key)
        note = {"text": text, "evidence": evidence.strip()[:600], "at": datetime.now(UTC).isoformat(), "run_id": ctx.run_id}
        state.notes = [*state.notes, note][-NOTES_KEPT:]
    return {"kept": True}


def _emit_event(ctx: Context, type: str, payload_json: str = "{}") -> dict[str, Any]:  # noqa: A002
    if type not in ctx.agent.emits:
        return {"error": f"{ctx.agent.name} does not send {type!r}. It may send: {', '.join(ctx.agent.emits) or 'nothing yet'}."}
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return {"error": "payload_json is not valid JSON."}
    if not isinstance(payload, dict):
        return {"error": "payload_json must be a JSON object."}
    with ctx.db() as session:
        event_id = events.emit(session, type, ctx.agent.key, {**payload, "run_id": ctx.run_id}).id
        ctx.emitted.append(event_id)
    return {"sent": event_id}


def _propose(ctx: Context, action_type: str, title: str, payload_json: str, reason: str = "", expected_effect: str = "", evidence: list[str] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return {"error": "payload_json is not valid JSON."}
    if not isinstance(payload, dict):
        return {"error": "payload_json must be a JSON object."}
    try:
        with ctx.db() as session:
            row = approvals.propose(
                session, agent=ctx.agent.key, action_type=action_type, title=title, payload=payload, reason=reason,
                expected_effect=expected_effect, evidence=[{"text": str(e)[:500]} for e in (evidence or [])[:10]], run_id=ctx.run_id or None,
            )
            proposal_id, status = row.id, row.status
            ctx.proposals.append(proposal_id)
    except approvals.ProposalRefused as exc:
        return {"error": str(exc)}
    if status == "approved":
        return {"proposal": proposal_id, "status": "will run alone within the owner's limit"}
    return {"proposal": proposal_id, "status": "waiting for the owner's approval"}


def _read_web_page(ctx: Context, url: str) -> dict[str, Any]:
    try:
        return web.fetch(url)
    except web.Blocked as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a page that cannot be read is a sentence for the model
        return {"error": f"The page could not be read ({exc.__class__.__name__})."}


def _report_directive(ctx: Context, directive_id: int, progress: str, done: bool = False) -> dict[str, Any]:
    with ctx.db() as session:
        row = session.get(OwnerDirective, int(directive_id))
        if row is None:
            return {"error": "No such directive."}
        try:
            owner_directives.report(session, row, agent=ctx.agent.key, text=progress, done=bool(done))
        except owner_directives.DirectiveRefused as exc:
            return {"error": str(exc)}
    return {"reported": True}


# --- Nia's tools: the whole team at a glance ---------------------------------------------------------
def _team_overview(ctx: Context) -> dict[str, Any]:
    from . import config, costs

    out = []
    with ctx.db() as session:
        day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        for key, agent in registry.AGENTS.items():
            status, waiting = config.status(session, key)
            last = session.execute(select(TeamRun).where(TeamRun.agent == key).order_by(TeamRun.started_at.desc()).limit(1)).scalar_one_or_none()
            pending = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.agent == key, TeamApproval.status == "pending")).scalar() or 0
            out.append({"agent": key, "name": agent.name, "department": agent.department, "status": status, "waiting_for": waiting,
                        "last_run": {"at": last.started_at.isoformat(), "status": last.status, "summary": last.summary[:600]} if last else None,
                        "pending_approvals": int(pending), "ai_cost_today_usd": costs.agent(session, key, day)})
        directives = [{"id": d.id, "text": d.text, "agents": d.agents, "status": d.status, "reports": d.reports[-3:]}
                      for d in session.execute(select(OwnerDirective).where(OwnerDirective.status.in_(("proposed", "active"))).order_by(OwnerDirective.id)).scalars()]
        from ...models import OutsideAgent

        outside = []
        for o in session.execute(select(OutsideAgent).where(OutsideAgent.status != "revoked").order_by(OutsideAgent.id)).scalars():
            last = session.execute(select(TeamRun).where(TeamRun.agent == o.key).order_by(TeamRun.started_at.desc()).limit(1)).scalar_one_or_none()
            pending = session.execute(select(func.count()).select_from(TeamApproval).where(TeamApproval.agent == o.key, TeamApproval.status == "pending")).scalar() or 0
            outside.append({"agent": o.key, "name": o.name, "kind": o.kind, "from": o.client_label or o.provider, "status": o.status, "desk": o.desk,
                            "last_seen": o.last_seen_at.isoformat() if o.last_seen_at else None,
                            "last_report": {"at": last.started_at.isoformat(), "summary": last.summary[:600]} if last else None, "pending_approvals": int(pending)})
    return {"agents": out, "outside_agents": outside, "open_directives": directives}


def _agent_report(ctx: Context, agent: str, runs: int = 3) -> dict[str, Any]:
    if agent not in registry.AGENTS:
        return {"error": f"No such agent. Agents: {', '.join(registry.AGENTS)}."}
    with ctx.db() as session:
        rows = session.execute(select(TeamRun).where(TeamRun.agent == agent).order_by(TeamRun.started_at.desc()).limit(max(1, min(int(runs), 10)))).scalars()
        return {"agent": agent, "runs": [{"at": r.started_at.isoformat(), "trigger": r.trigger, "status": r.status, "summary": r.summary[:2000],
                                          "tools": [c.get("tool") for c in r.tool_calls], "cost_usd": r.cost_usd} for r in rows]}


def _approvals_waiting(ctx: Context) -> dict[str, Any]:
    with ctx.db() as session:
        rows = session.execute(select(TeamApproval).where(TeamApproval.status == "pending").order_by(TeamApproval.id).limit(100)).scalars()
        items = [{"id": a.id, "agent": a.agent, "action": a.action_type, "level": a.level, "title": a.title, "reason": a.reason[:300], "since": a.created_at.isoformat()} for a in rows]
    return {"waiting": items, "count": len(items)}


def _read_memories(ctx: Context, query: str = "") -> dict[str, Any]:
    with ctx.db() as session:
        rows = nia_memory.search(session, query) if query else nia_memory.recent(session, 30)
        return {"memories": [{"id": m.id, "kind": m.kind, "topic": m.topic, "text": m.text, "evidence": m.evidence, "since": m.created_at.date().isoformat()} for m in rows]}


def _search_brain(ctx: Context, query: str, kind: str = "") -> dict[str, Any]:
    """The team's shared knowledge (Admin → Brain): what the other agents noted, proposed and were told, by words."""
    with ctx.db() as session:
        graph = brain.build(session, for_agent=ctx.agent.key)
    kinds = (kind,) if kind in brain.KINDS else ()
    found = brain.search(graph, query, limit=BRAIN_FOUND, kinds=kinds)
    return {"found": found} if found else {"found": [], "note": "Nothing in the brain matches these words."}


def _proposal_status(ctx: Context, ids: list[int] | None = None) -> dict[str, Any]:
    """What really became of proposals (yours of the last days, or the ids asked) and of Nia's directives: before telling the owner
    something is done, look here."""
    from ..nia import truth

    with ctx.db() as session:
        if ids:
            rows = session.execute(select(TeamApproval).where(TeamApproval.id.in_([int(i) for i in ids][:20]))).scalars().all()
            proposals = [{"id": r.id, "agent": r.agent, "what": r.title, "status": r.status, **({"why_failed": truth._reason(r)} if r.status == "failed" else {})}
                         for r in rows]
            return {"proposals": proposals, "note": "pending = waiting for the owner; executed = done; failed = not done; rejected/withdrawn = not done."}
        seen = truth.recent(session, ctx.agent.key)
        return {**seen, "note": "pending = waiting for the owner; executed = done; failed = not done. Directives: proposed = waiting for the owner's OK; active = the agents have it."}


def _confirm_directive(ctx: Context, directive_id: int) -> dict[str, Any]:
    """The owner said yes to a directive Nia read back: it goes to the agents."""
    if ctx.admin_id is None:
        return {"error": "Only while the owner is talking with you, after they said yes to this directive."}
    with ctx.db() as session:
        row = session.get(OwnerDirective, int(directive_id))
        if row is None or row.status != "proposed":
            return {"error": "No such directive waiting for the owner's OK."}
        owner_directives.confirm(session, row)
        return {"directive": row.id, "status": row.status, "agents": row.agents, "note": "Active: the agents read it on their next run."}


def _read_admin_docs(_ctx: Context, query: str = "", section: str = "", page: str = "") -> dict[str, Any]:
    """The admin's own documentation (Admin → Docs): the same text the owner reads."""
    from ... import admin_docs

    if section or page:
        found = admin_docs.get(section) if section else admin_docs.for_screen(page)
        if found is None:
            return {"error": "No such section. Call read_admin_docs without arguments for the list."}
        return {"key": found.key, "title": found.title, "pages": list(found.screens), "text": found.body[:16000]}
    if query:
        return {"results": [{"section": s.key, "title": s.title, "line": line} for s, line in admin_docs.search(query)]}
    return {"sections": [{"section": s.key, "title": s.title, "summary": s.summary, "pages": list(s.screens)} for s in admin_docs.sections()]}


def _admin_endpoints(_ctx: Context, section: str = "", path_prefix: str = "") -> dict[str, Any]:
    """The admin's functions for one Docs section (or under one address), with what each takes."""
    from ... import admin_docs
    from . import admin_changes

    prefixes: list[str] = []
    if section:
        found = admin_docs.get(section)
        if found is None:
            return {"error": "No such section. read_admin_docs lists them."}
        prefixes = list(found.api)
    if path_prefix:
        prefixes.append(path_prefix)
    if not prefixes:
        return {"error": "Give a Docs section (e.g. team) or an address prefix (e.g. /admin-api/team)."}
    return {"endpoints": admin_changes.describe(prefixes)[:60]}


def _propose_admin_change(ctx: Context, method: str, path: str, title: str, body_json: str = "", query_json: str = "", reason: str = "",
                          expected_effect: str = "") -> dict[str, Any]:
    from . import admin_changes

    try:
        body = json.loads(body_json) if body_json.strip() else None
        query = json.loads(query_json) if query_json.strip() else {}
        payload = admin_changes.check({"method": method, "path": path, "body": body, "query": query})
    except json.JSONDecodeError:
        return {"error": "body_json and query_json must be valid JSON."}
    except admin_changes.Refused as exc:
        return {"error": str(exc)}
    try:
        with ctx.db() as session:
            row = approvals.propose(session, agent=ctx.agent.key, action_type=admin_changes.ACTION, title=title, payload=payload, reason=reason,
                                    expected_effect=expected_effect, run_id=ctx.run_id or None)
            proposal_id = row.id
            ctx.proposals.append(proposal_id)
    except approvals.ProposalRefused as exc:
        return {"error": str(exc)}
    return {"proposal": proposal_id, "status": "waiting in Approvals; it happens when the owner approves it on screen"}


def _remember(ctx: Context, kind: str, text: str, evidence: str = "", topic: str = "") -> dict[str, Any]:
    try:
        with ctx.db() as session:
            row = nia_memory.add(session, kind=kind, text=text, evidence=evidence, topic=topic,
                                 source=f"conversation {ctx.conversation_id}" if ctx.conversation_id else f"run {ctx.run_id}")
            memory_id = row.id
    except ValueError as exc:
        return {"error": str(exc)}
    return {"remembered": memory_id}


def _propose_directive(ctx: Context, text: str, agents: list[str] | None = None, goal: str = "", due_on: str = "") -> dict[str, Any]:
    from datetime import date as date_

    try:
        due = date_.fromisoformat(due_on) if due_on else None
    except ValueError:
        return {"error": "due_on is a date (YYYY-MM-DD) or empty."}
    try:
        with ctx.db() as session:
            row = owner_directives.propose(session, text=text, agents=list(agents or []), goal=goal, due_on=due, conversation_id=ctx.conversation_id)
            directive_id = row.id
    except owner_directives.DirectiveRefused as exc:
        return {"error": str(exc)}
    ctx.directives.append(directive_id)
    return {"directive": directive_id, "status": "shown to the owner to confirm; it reaches the agents only after the owner confirms"}


# --- quality and compliance -------------------------------------------------------------------------
def _run_checks(ctx: Context) -> dict[str, Any]:
    from ..qa import checks

    with ctx.db() as session:
        pages = checks.pages(session)
        results = checks.run(session)
        checks.record(session, results, "agent")
    return {"checks": results, "failed": [r for r in results if not r["ok"]], "slow": [r for r in results if r.get("slow")],
            "pages": "the owner's pages" if pages else "only the admin: the owner has not listed pages to check (Admin → Product → Checks)"}


def _qa_history(ctx: Context, runs: int = 20) -> dict[str, Any]:
    from ..qa import checks

    with ctx.db() as session:
        return checks.history(session, max(1, min(runs, 50)))


def _ops_signals(ctx: Context) -> dict[str, Any]:
    from ..qa import ops

    with ctx.db() as session:
        return ops.signals(session)


def _incidents(ctx: Context) -> dict[str, Any]:
    from ..qa import incidents

    with ctx.db() as session:
        return incidents.listing(session)


def _open_incident(ctx: Context, title: str, severity: str, key: str, impact: str = "", cause: str = "", fix: str = "", note: str = "") -> dict[str, Any]:
    from ..qa import incidents

    with ctx.db() as session:
        try:
            row, new = incidents.open_or_note(session, title=title, severity=severity, key=key, impact=impact, cause=cause, fix=fix, note=note, by=ctx.agent.key)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"incident_id": row.id, "new": new, "status": row.status}


def _update_incident(ctx: Context, incident_id: int, note: str = "", status: str = "", severity: str = "", cause: str = "", fix: str = "", impact: str = "") -> dict[str, Any]:
    from ..qa import incidents

    with ctx.db() as session:
        try:
            row = incidents.update(session, incident_id, by=ctx.agent.key, note=note, status=status or None, severity=severity or None,
                                   cause=cause or None, fix=fix or None, impact=impact or None)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"incident_id": row.id, "status": row.status}


def _product_ideas(ctx: Context) -> dict[str, Any]:
    from ..product import ideas

    with ctx.db() as session:
        return {"ideas": ideas.listing(session)}


def _measure_idea(ctx: Context, idea_id: int, verdict: str, note: str) -> dict[str, Any]:
    from ..product import ideas

    with ctx.db() as session:
        try:
            row = ideas.measure(session, idea_id, verdict, note)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"idea_id": row.id, "baseline": row.baseline, "result": row.result, "verdict": row.verdict}


# --- trust -------------------------------------------------------
# --- candidates and content -------------------------------------------------------------------------
def _social_overview(ctx: Context) -> dict[str, Any]:
    from ..social import posts

    with ctx.db() as session:
        return posts.overview(session)


# --- the web ------------------------------------------------------------------------------------------------
def _web_search(ctx: Context, query: str) -> dict[str, Any]:
    from ...ai.client import record_usage
    from .. import websearch as search
    from . import config, runner

    query = query.strip()[:400]
    if len(query) < 3:
        return {"error": "Write what to search for."}
    if ctx.scout is not None:
        if ctx.searches >= ctx.scout.searches_per_run:
            return {"error": f"That was your last search this run ({ctx.scout.searches_per_run}). Work with what you found."}
        ctx.searches += 1
    with ctx.db() as session:
        problem = runner.budget_problem(session, ctx.agent.key, runs=ctx.run_id > 0)
        tier = config.agent(session, ctx.agent.key).tier or "fast"
    if problem:
        return {"error": problem}
    found = search.SEARCH(query, tier)
    with ctx.db() as session:
        record_usage(model=found.model or "search", operation=f"agent.{ctx.agent.key}.search", agent_type="team", input_tokens=found.input_tokens,
                     output_tokens=found.output_tokens, latency_ms=found.latency_ms, success=True, session=session)
    return search.as_result(found)


# --- the research team (stage E5, services/team/research_work.py) -----------------------------------------------
# --- travel knowledge (stage E6, services/team/travel_work.py) -----------------------------------------------------
# --- product, money, health ---------------------------------------------------------------------------
def _product_numbers(ctx: Context, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    with ctx.db() as session:
        kpis = [{"key": k.key, **{f: queries.kpi(session, k, days)[f] for f in ("value", "previous")}} for k in queries.KPIS]
        return {"days": days, "kpis": kpis, "web_vitals": queries.vitals(session, days)}


def _health_numbers(ctx: Context) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=1)
    with ctx.db() as session:
        return {"open_errors": queries.errors(session, "open", 20), "alerts_7_days": queries.alerts(session, 7), "uptime_24h": queries.uptime(session, since),
                "slowest_routes_24h": queries.performance(session, 24)["slowest"]}


# --- marketing ----------------------------------------------------------------
def _brand_kit(ctx: Context) -> dict[str, Any]:
    from ..marketing import brand, studio

    with ctx.db() as session:
        wanted = brand.settings_of(session)
        return {"brand": brand.kit(session).model_dump(), "settings": wanted.model_dump(), "picture_budget": studio.budget(session)}


def _media_library(ctx: Context, kind: str = "", limit: int = 30) -> dict[str, Any]:
    from ...models import MarketingMedia

    with ctx.db() as session:
        stmt = select(MarketingMedia).order_by(MarketingMedia.id.desc()).limit(max(1, min(int(limit), 100)))
        if kind in ("image", "video"):
            stmt = stmt.where(MarketingMedia.kind == kind)
        rows = list(session.execute(stmt).scalars())
        return {"media": [{"id": m.id, "kind": m.kind, "source": m.source, "title": m.title, "width": m.width, "height": m.height, "ai_generated": m.ai_generated,
                           "shows_people": m.shows_people, "alt_text": m.alt_text, "created_by": m.created_by, "created_at": m.created_at.isoformat()} for m in rows]}


def _create_image(ctx: Context, prompt: str, aspect: str = "4:5", headline: str = "", subline: str = "", shows_people: bool = False, title: str = "",
                  alt_text: str = "", with_character: bool = False) -> dict[str, Any]:
    from ..marketing import studio

    try:
        with ctx.db() as session:
            row = studio.make(session, prompt=prompt, aspect=aspect, headline=headline, subline=subline, shows_people=bool(shows_people), title=title,
                              alt_text=alt_text, created_by=ctx.agent.key, with_character=bool(with_character))
            return {"media_id": row.id, "width": row.width, "height": row.height, "cost_usd": row.cost_usd, "budget": studio.budget(session),
                    "note": "Use this media_id in social.post (media_ids)."}
    except studio.StudioRefused as exc:
        return {"error": str(exc)}


def _storyboard_frame(ctx: Context, moment: str, outfit: str, setting: str, continue_from: int = 0) -> dict[str, Any]:
    from ..marketing import studio

    try:
        with ctx.db() as session:
            row = studio.frame(session, moment=moment, outfit=outfit, setting=setting, continue_from=int(continue_from) or None, created_by=ctx.agent.key)
            return {"media_id": row.id, "cost_usd": row.cost_usd, "budget": studio.budget(session),
                    "note": "Use this media_id as the scene's start_frame; give it as continue_from for the next scene's frame."}
    except studio.StudioRefused as exc:
        return {"error": str(exc)}


def _character(ctx: Context) -> dict[str, Any]:
    from ..marketing import character

    with ctx.db() as session:
        figure = character.get(session)
        pictures = character.pictures(session, figure)
    if not figure.ready or not pictures:
        return {"ready": False, "note": "The owner has not set up the company's character yet (Admin → Marketing → Character: its name and pictures). No video with it can be made."}
    return {"ready": True, "name": figure.name, "who": figure.who, "look": figure.look, "personality": figure.personality, "voice": figure.voice, "rules": figure.rules,
            "pictures": len(pictures), "note": "Always this same person; clothes, places and situations may change."}


def _video_studio(ctx: Context, limit: int = 12) -> dict[str, Any]:
    from ...models import MarketingVideo
    from ..marketing import brand, video_studio

    with ctx.db() as session:
        wanted = brand.settings_of(session)
        rows = list(session.execute(select(MarketingVideo).order_by(MarketingVideo.id.desc()).limit(max(1, min(int(limit), 40)))).scalars())
        videos = []
        for row in rows:
            seen = video_studio.view(session, row)
            concept = row.concept or {}
            videos.append({"id": row.id, "title": row.title, "status": row.status, "series": concept.get("series"), "idea": str(concept.get("idea") or "")[:300],
                           "hook": concept.get("hook"), "seconds": seen["seconds"], "media_id": row.media_id, "made_at": row.made_at.isoformat() if row.made_at else None,
                           "error": row.error, "posts": [{"network": p["network"], "status": p["status"]} for p in seen["posts"]],
                           "review": {k: seen["review"].get(k) for k in ("verdict", "summary", "mistakes", "false_claims", "brand_in_english")} if seen["review"] else None})
        waiting = [{"id": a.id, "title": a.title, "created_at": a.created_at.isoformat()}
                   for a in session.execute(select(TeamApproval).where(TeamApproval.action_type == "video.concept", TeamApproval.status == "pending")).scalars()]
        return {"rhythm": video_studio.rhythm(session), "budget": video_studio.budget(session), "quality": wanted.video_quality,
                "scene_seconds": list(video_studio.SCENE_SECONDS), "max_scenes": video_studio.MAX_SCENES, "videos": videos, "concepts_waiting": waiting,
                "note": "rhythm.left is how many more videos this day or week may have (None: the owner set no number — then none is proposed)."}


def _content_calendar(ctx: Context, days_back: int = 14, days_ahead: int = 21) -> dict[str, Any]:
    from datetime import timedelta

    from ...models import SocialPost

    now = datetime.now(UTC)
    with ctx.db() as session:
        rows = session.execute(select(SocialPost).where(or_(SocialPost.scheduled_at.between(now - timedelta(days=int(days_back)), now + timedelta(days=int(days_ahead))),
                                                         SocialPost.posted_at >= now - timedelta(days=int(days_back)), SocialPost.status.in_(("ready", "needs_video", "scheduled"))))
                               .order_by(SocialPost.id.desc()).limit(80)).scalars()
        posts = [{"id": p.id, "network": p.network, "kind": p.kind, "status": p.status, "text": p.text[:160], "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                  "posted_at": p.posted_at.isoformat() if p.posted_at else None, "agent": p.agent} for p in rows]
        waiting = [{"id": a.id, "agent": a.agent, "title": a.title, "networks": (a.edited_payload or a.payload).get("networks"), "created_at": a.created_at.isoformat()}
                   for a in session.execute(select(TeamApproval).where(TeamApproval.action_type == "social.post", TeamApproval.status == "pending")).scalars()]
    return {"posts": posts, "waiting_for_approval": waiting}


def _social_results(ctx: Context, days: int = 30) -> dict[str, Any]:
    from ..social import posts

    with ctx.db() as session:
        return posts.results(session, max(1, min(int(days), 180)))


def _marketing_plans(ctx: Context) -> dict[str, Any]:
    from ...models import MarketingPlan
    from ..marketing import plans

    with ctx.db() as session:
        active = plans.active(session)
        recent = list(session.execute(select(MarketingPlan).order_by(MarketingPlan.id.desc()).limit(4)).scalars())
        return {"active": plans.view(active) if active else None, "recent": [{"id": p.id, "title": p.title, "period": f"{p.period_start}..{p.period_end}", "status": p.status}
                                                                              for p in recent]}


def _browse(ctx: Context, goal: str, url: str = "") -> dict[str, Any]:
    """Nia's browser: start working on a goal in a real browser the owner watches on the Nia page."""
    from ..nia import browser

    if ctx.admin_id is None:
        return {"error": "Only the owner, talking to you now, can start the browser."}
    with ctx.db() as session:
        try:
            row = browser.start(session, admin_id=ctx.admin_id, goal=goal, url=url, where="cloud")
        except browser.Refused as exc:
            return {"error": str(exc)}
        return {"session_id": row.id, "status": row.status,
                "tell_the_owner": "I'm opening the browser now — you can watch me on the Nia page, and I'll ask before anything that sends, buys or signs in."}


def _browser_status(ctx: Context) -> dict[str, Any]:
    from ...models import BrowserSession
    from ..nia import browser

    with ctx.db() as session:
        row = browser.active(session) or session.execute(select(BrowserSession).order_by(BrowserSession.id.desc()).limit(1)).scalar_one_or_none()
        if row is None:
            return {"session": None}
        view = browser.view(session, row, steps=12)
        return {"session": {k: view[k] for k in ("id", "goal", "status", "reason", "url", "question", "asks_in_words", "result", "turns", "cost_usd")},
                "last_steps": [st["text"] for st in view["steps"]]}


def _browser_answer(ctx: Context, decision: str, text: str = "") -> dict[str, Any]:
    """The owner's answer by voice or chat: allow, take_over, hand_back, pause, resume, stop — or words for Nia's question."""
    from ..nia import browser

    if ctx.admin_id is None:
        return {"error": "Only the owner, talking to you now, can answer."}
    with ctx.db() as session:
        row = browser.active(session)
        if row is None:
            return {"error": "Nia is not browsing now."}
        try:
            if decision == "reply":
                browser.reply(session, row, text)
            else:
                browser.answer(session, row, decision, note=text)
        except browser.Refused as exc:
            return {"error": str(exc)}
        return {"status": row.status}


# --- Nia running the team for the owner: an agent's whole picture, the admin's settings, starting and asking agents ---
# Platform settings Nia never reads: passwords, tokens and developer-app secrets live in them.
SETTINGS_HIDDEN = ("qa", "mailboxes", "social_apps", "social_oauth_states")
_SECRET_WORDS = ("secret", "password", "passwd", "token", "api_key", "apikey", "private_key", "credential", "ciphertext", "cookie", "totp")
SETTING_TEXT_LIMIT = 30_000  # characters of one setting handed to the model


def _hide_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("(hidden)" if isinstance(v, str) and v and any(w in str(k).lower() for w in _SECRET_WORDS) else _hide_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_hide_secrets(v) for v in value]
    return value


def _agent_templates(_ctx: Context) -> dict[str, Any]:
    """What a new agent can be made of, for proposing one (agent.create): the starter templates, the tools and actions the
    template offers, the Insights numbers a goal can be, and the rules a design must keep."""
    from . import agents, templates

    return {
        "templates": [{"key": t.key, "name": t.name, "department": t.department, "job": t.job, "tools": list(t.tools), "actions": list(t.actions),
                       "subscribes": list(t.subscribes), "emits": list(t.emits), "kpi": t.kpi} for t in templates.TEMPLATES],
        "tools": {k: CATALOGUE[k][1] for k in agents.offered_tools()},
        "every_agent_has": list(registry.COMMON_TOOLS),
        "actions": {k: registry.ACTIONS[k].label for k in agents.offered_actions()},
        "kpis": list(queries.BY_KEY),
        "departments": registry.departments(),
        "rules": [f"a job of at least {agents.MIN_JOB} characters", "at least one tool besides the ones every agent has",
                  "event names like 'marketing.brief'", f"at most {agents.MAX_ROUNDS} rounds", "never Nia's own tools or admin.change / agent.create"],
    }


def _agent_profile(ctx: Context, agent: str) -> dict[str, Any]:
    """One agent's whole picture: its design (job, how it works, tools, the actions it may propose and their shape), the
    owner's settings for it, its current playbook and its last runs. Read before writing or changing its playbook or settings."""
    from . import config

    spec = registry.get(agent)
    if spec is None:
        return {"error": f"No such agent. Agents: {', '.join(registry.AGENTS)}."}
    with ctx.db() as session:
        mine = config.agent(session, spec.key)
        status, waiting = config.status(session, spec.key)
        playbook = approvals.latest_playbook(session, spec.key)
        runs = session.execute(select(TeamRun).where(TeamRun.agent == spec.key).order_by(TeamRun.started_at.desc()).limit(3)).scalars()
        recent = [{"at": r.started_at.isoformat(), "trigger": r.trigger, "status": r.status, "summary": r.summary[:800]} for r in runs]
        actions = [{"key": a, "label": registry.ACTIONS[a].label, "ceiling": registry.ACTIONS[a].ceiling, "level": config.level_of(mine, a),
                    "payload": registry.ACTIONS[a].payload} for a in registry.actions_of(spec)]
        return {
            "key": spec.key, "name": spec.name, "department": spec.department, "purpose": spec.purpose,
            "built_in_guide": spec.guide, "status": status, "waiting_for": waiting,
            "settings": mine.model_dump(), "schedule": config.schedule_text(mine.schedule),
            "tools": {t: CATALOGUE[t][1] for t in registry.tools_of(spec) if t in CATALOGUE},
            "tools_not_ticked": [t for t in registry.tools_of(spec) if t in CATALOGUE and t not in mine.tools],
            "actions": actions, "subscribes": list(spec.subscribes), "emits": list(spec.emits),
            "playbook": {"version": playbook.version, "text": playbook.text, "note": playbook.note} if playbook else None,
            "last_runs": recent,
            "how_to_change": {"playbook": f"PUT /admin-api/team/agents/{spec.key}/playbook with {{\"text\": \"the whole playbook\", \"note\": \"what changed\"}}",
                              "settings": f"PUT /admin-api/team/agents/{spec.key} with the whole settings object above, changed where the owner said"},
        }


def _read_settings(ctx: Context, key: str = "") -> dict[str, Any]:
    """The admin's current values of one platform setting (secrets hidden), or the list of settings. Read before a change."""
    from .. import platform

    readable = [k for k in platform.defaults() if k not in SETTINGS_HIDDEN]
    if not key:
        return {"settings": readable, "note": "Call again with one key. The team and each agent: agent_profile."}
    if key not in readable:
        return {"error": "This setting holds passwords or keys; it stays on its admin page." if key in SETTINGS_HIDDEN else f"No such setting. Settings: {', '.join(readable)}."}
    with ctx.db() as session:
        value = _hide_secrets(platform.get(session, key))
        if key == "agents":
            from . import config

            value = {"team": config.team(session).model_dump(), "agents": value}
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > SETTING_TEXT_LIMIT:
        return {"key": key, "value_cut": text[:SETTING_TEXT_LIMIT], "note": "Longer than shown; ask the owner for the rest before changing it as a whole."}
    return {"key": key, "value": value}


def _run_agent(ctx: Context, agent: str) -> dict[str, Any]:
    """Start one agent's run now, because the owner just asked for it."""
    from . import config, runner, tasks

    if ctx.admin_id is None:
        return {"error": "Only the owner, talking to you now, can start an agent."}
    spec = registry.get(agent)
    if spec is None or spec.key == "nia":
        return {"error": f"No such agent. Agents: {', '.join(k for k in registry.AGENTS if k != 'nia')}."}
    with ctx.db() as session:
        status, waiting = config.status(session, spec.key)
        if status != "ready":
            reason = {"unavailable": "is not part of the team", "off": "is switched off"}.get(status, "waits for: " + ", ".join(waiting))
            return {"error": f"{spec.name} {reason}. The owner sets it in Team → {spec.name}."}
        problem = runner.budget_problem(session, spec.key)
        if problem:
            return {"error": problem}
        queued = tasks.start(session, spec.key, "manual")
    if not queued:
        return {"agent": spec.name, "queued": False, "note": "A run of this agent is already waiting or running; read its result with agent_report later."}
    return {"agent": spec.name, "queued": True, "note": "It starts within a minute and usually takes a few minutes; read its result with agent_report."}


def _ask_agent(ctx: Context, agent: str, question: str) -> dict[str, Any]:
    """Put the owner's question to one agent and bring back its answer (a conversation in that agent's chat)."""
    from ...models import TeamConversation
    from . import chat as agent_chat

    if ctx.admin_id is None:
        return {"error": "Only the owner, talking to you now, can have you ask an agent."}
    if agent.startswith("ext_"):
        # An outside agent works on its own clock: the question waits in its messages (my_messages) and its answer comes in its report.
        from ...models import OutsideAgent, OutsideAgentMessage

        with ctx.db() as session:
            outsider = session.execute(select(OutsideAgent).where(OutsideAgent.key == agent, OutsideAgent.status != "revoked")).scalar_one_or_none()
            if outsider is None:
                return {"error": "No such outside agent."}
            text = " ".join(str(question).split())[:4000]
            session.add(OutsideAgentMessage(agent_id=outsider.id, sender="nia", text=f"From Nia, for the owner: {text}", created_at=datetime.now(UTC)))
            return {"agent": outsider.name, "left_message": True,
                    "note": f"{outsider.name} is an outside agent: it reads the question the next time it works and answers in its report (Team → Outside agents)."}
    spec = registry.get(agent)
    if spec is None or spec.key == "nia":
        return {"error": f"No such agent. Agents: {', '.join(k for k in registry.AGENTS if k != 'nia')}."}
    question = " ".join(str(question).split())[:4000]
    if not question:
        return {"error": "Give the question."}
    with ctx.db() as session:
        problem = agent_chat.ready(session, spec.key)
        if problem:
            return {"error": problem}
        now = datetime.now(UTC)
        row = TeamConversation(agent=spec.key, title=("Nia asks: " + question)[:200], admin_id=ctx.admin_id, created_at=now, updated_at=now)
        session.add(row)
        session.flush()
        conversation_id = row.id
    try:
        said = agent_chat.answer(ctx.db, agent_key=spec.key, conversation_id=conversation_id, admin_id=ctx.admin_id,
                                 text=f"Nia asks you this for the owner: {question}")
    except agent_chat.Unavailable as exc:
        return {"error": str(exc)}
    return {"agent": spec.name, "answer": said.text[:8000], "proposals": said.proposals, "cost_usd": round(said.cost_usd, 4),
            "note": f"The owner can read it in Team → {spec.name} → Chat. Its proposals wait in Approvals."}



# --- Nia on the owner's computer -------------------------------------------------------
READ_LIMIT = 40_000  # characters of a file's text handed to the model at once
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".log", ".ini", ".yaml", ".yml"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
SHOWABLE = TEXT_TYPES - {".html", ".htm", ".xml", ".ini", ".yaml", ".yml"} | set(IMAGE_TYPES) | {".pdf"}


def _decide_approvals(ctx: Context, ids: list[int], decision: str, note: str = "") -> dict[str, Any]:
    if ctx.admin_id is None:
        return {"error": "Only the owner, talking to you now, can decide."}
    if decision not in ("approve", "reject"):
        return {"error": "decision is approve or reject."}
    with ctx.db() as session:
        rows = list(session.execute(select(TeamApproval).where(TeamApproval.id.in_([int(i) for i in ids][:50]))).scalars())
        always = [r.id for r in rows if r.level == "L3"]
        if always:
            return {"error": f"{always} are decisions the owner confirms on screen (Approvals), never by voice or chat."}
        changed = approvals.decide(session, rows, "approve" if decision == "approve" else "reject", admin_id=ctx.admin_id, note=note)
        return {"decided": [r.id for r in changed], "not_pending": [r.id for r in rows if r not in changed]}


Tool = tuple[Callable[..., dict[str, Any]], str, dict[str, Any], list[str]]

CATALOGUE: dict[str, Tool] = {
    "read_insights": (_read_insights, "The numbers from Insights for the last N days: every number with the previous period.",
                      {"days": {"type": "INTEGER", "description": "Days to look back (1-365)."}}, []),
    "read_events": (_read_events, "The team's latest events (handoffs), newest first, optionally of one type.",
                    {"type": {"type": "STRING"}, "limit": {"type": "INTEGER"}}, []),
    "search_brain": (_search_brain, "Search the team's shared brain by words: every agent's notes, the proposals and the owner's decisions on them, "
                     "Nia's memories of the owner's decisions and the owner's directives — each with what it is linked to. "
                     "Read it before you work on a topic another agent may already know about. Optional kind: " + ", ".join(brain.KINDS) + ".",
                     {"query": {"type": "STRING"}, "kind": {"type": "STRING"}}, ["query"]),
    "proposal_status": (_proposal_status, "What really became of your proposals of the last days (or the ids you give) and of Nia's directives: waiting, done, "
                        "failed (with the reason) or rejected. Check it before telling the owner something is done or approved.",
                        {"ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}}, []),
    "confirm_directive": (_confirm_directive, "Confirm one of your directives after the owner said yes to it (read it back first); it then reaches the agents.",
                          {"directive_id": {"type": "INTEGER"}}, ["directive_id"]),
    "keep_note": (_keep_note, "Keep a short lesson for your later runs, with its evidence (numbers, ids). Never personal data.",
                  {"text": {"type": "STRING"}, "evidence": {"type": "STRING"}}, ["text"]),
    "emit_event": (_emit_event, "Tell the team something happened (a handoff), as an event type you may send.",
                   {"type": {"type": "STRING"}, "payload_json": {"type": "STRING", "description": "A JSON object with the facts (ids and numbers, no personal data)."}}, ["type"]),
    "web_search": (_web_search, "Search the web (Google): an answer with its source links. Confirm every fact on its official page (read_web_page) before using it.",
                   {"query": {"type": "STRING"}}, ["query"]),
    "read_web_page": (_read_web_page, "Read a public web page (for example a company's official page or a public rule) as text with its links.",
                      {"url": {"type": "STRING"}}, ["url"]),
    "report_directive": (_report_directive, "Report progress on one of the owner's directives to you (and whether it is done).",
                         {"directive_id": {"type": "INTEGER"}, "progress": {"type": "STRING"}, "done": {"type": "BOOLEAN"}}, ["directive_id", "progress"]),
    "team_overview": (_team_overview, "Every agent: status, what it waits for, its last run's summary, approvals waiting, AI cost today; and the open directives.", {}, []),
    "agent_report": (_agent_report, "One agent's latest runs with their summaries.", {"agent": {"type": "STRING"}, "runs": {"type": "INTEGER"}}, ["agent"]),
    "approvals_waiting": (_approvals_waiting, "What waits for the owner's approval.", {}, []),
    "read_admin_docs": (_read_admin_docs, "How the admin works, from Admin → Docs (the text the owner reads): no arguments for the list of sections; "
                        "query to search; section (a key) or page (an admin address like /team) to read one in full.",
                        {"query": {"type": "STRING"}, "section": {"type": "STRING"}, "page": {"type": "STRING"}}, []),
    "admin_endpoints": (_admin_endpoints, "The admin's server functions for one Docs section (section key) or under an address (path_prefix): method, path, "
                        "what it does, its query names and the exact body it takes. Use it before propose_admin_change.",
                        {"section": {"type": "STRING"}, "path_prefix": {"type": "STRING"}}, []),
    "propose_admin_change": (_propose_admin_change, "Propose one exact change in the admin (POST, PUT, PATCH or DELETE on an /admin-api/ address, with the complete "
                             "JSON body). It waits in Approvals as the owner's decision and is carried out by the owner's own admin page when approved. "
                             "Never for sign-in, admin accounts, authenticator recovery or approvals.",
                             {"method": {"type": "STRING"}, "path": {"type": "STRING"}, "title": {"type": "STRING", "description": "What changes, in one line."},
                              "body_json": {"type": "STRING", "description": "The request body as JSON."}, "query_json": {"type": "STRING"},
                              "reason": {"type": "STRING"}, "expected_effect": {"type": "STRING"}}, ["method", "path", "title"]),
    "read_memories": (_read_memories, "Search Nia's memory (the owner's decisions, preferences, lessons, facts).", {"query": {"type": "STRING"}}, []),
    "remember": (_remember, "Remember something for later: kind person (about the owner: who they are, family, how they work), decision (the owner "
                 "decided), preference (how the owner likes things), work (what you and the owner did together: what was done, the result, what is "
                 "still open), lesson (learned from results, with evidence) or fact. topic: one or two words it belongs to (website, "
                 "customers, finance, Nia, a country …) — the branch of the memory tree.",
                 {"kind": {"type": "STRING"}, "text": {"type": "STRING"}, "evidence": {"type": "STRING"}, "topic": {"type": "STRING"}}, ["kind", "text"]),
    "propose_directive": (_propose_directive, "Turn the owner's instruction into a directive for agents (keys from team_overview). The owner confirms it on screen before it reaches them.",
                          {"text": {"type": "STRING"}, "agents": {"type": "ARRAY", "items": {"type": "STRING"}}, "goal": {"type": "STRING"}, "due_on": {"type": "STRING"}}, ["text", "agents"]),
    "run_checks": (_run_checks, "Run every check now and keep the run: the admin and the owner's pages, their security headers and certificates. "
                   "Read-only.", {}, []),
    "qa_history": (_qa_history, "The last check runs: what fails now (since when, runs in a row), flaky checks (pass rate), slow checks, the owner's QA rules.",
                   {"runs": {"type": "INTEGER"}}, []),
    "ops_signals": (_ops_signals, "The system's own signals: failed and dead background tasks with their error line, the waiting queue and stuck tasks, late or failing "
                    "scheduled jobs, undelivered e-mails and phone notifications, certificates, disk and database size, plus open errors, alerts, uptime and slow routes.", {}, []),
    "incidents": (_incidents, "Open incidents and the recently resolved ones, with their timeline.", {}, []),
    "open_incident": (_open_incident, "Write down an incident (internal, no approval). key: what it is about, so it is not opened twice (an open one with the same key gets "
                      "your note instead). severity: critical, major or minor.",
                      {"title": {"type": "STRING"}, "severity": {"type": "STRING"}, "key": {"type": "STRING"}, "impact": {"type": "STRING"}, "cause": {"type": "STRING"},
                       "fix": {"type": "STRING"}, "note": {"type": "STRING"}}, ["title", "severity", "key"]),
    "update_incident": (_update_incident, "Add a note to an incident, or change its status (open, monitoring, resolved), severity, impact, cause or fix.",
                        {"incident_id": {"type": "INTEGER"}, "note": {"type": "STRING"}, "status": {"type": "STRING"}, "severity": {"type": "STRING"},
                         "cause": {"type": "STRING"}, "fix": {"type": "STRING"}, "impact": {"type": "STRING"}}, ["incident_id"]),
    "product_ideas": (_product_ideas, "The product ideas: accepted (with baseline), shipped (when ready_to_measure: the number after), measured (verdict) or dropped.", {}, []),
    "measure_idea": (_measure_idea, "Write down what a shipped idea did once its measuring days are over: verdict better, worse, no_change or too_little_data, and a note.",
                     {"idea_id": {"type": "INTEGER"}, "verdict": {"type": "STRING"}, "note": {"type": "STRING"}}, ["idea_id", "verdict", "note"]),
    "social_overview": (_social_overview, "The owner's social networks and the latest posts (waiting, posted, discarded).", {}, []),
    "brand_kit": (_brand_kit, "The company's brand kit (what it does, voice, colours, picture style, words to use and avoid, hashtags, languages, the AI label), the owner's marketing settings "
                  "(posts per network and day, posting hours) and what is left of this month's picture budget.", {}, []),
    "media_library": (_media_library, "Pictures and videos in the media library (newest first): id, made or uploaded, size, whether AI-made and showing people.",
                      {"kind": {"type": "STRING"}, "limit": {"type": "INTEGER"}}, []),
    "create_image": (_create_image, "Make one professional picture in the brand's style (costs money from the owner's monthly picture budget). prompt: the scene in one or two "
                     "sentences (who, doing what, where, light, mood); aspect: 4:5 (Instagram/Facebook feed), 1:1, 9:16 (stories), 16:9 (LinkedIn/YouTube); headline/subline: "
                     "optional short text set on a band in the brand's colours; shows_people: true when people appear; with_character: true to show the company's "
                     "character (its pictures are used, so it is the same person). Returns a media_id for social.post (or a storyboard frame for video.concept).",
                     {"prompt": {"type": "STRING"}, "aspect": {"type": "STRING"}, "headline": {"type": "STRING"}, "subline": {"type": "STRING"}, "shows_people": {"type": "BOOLEAN"},
                      "title": {"type": "STRING"}, "alt_text": {"type": "STRING"}, "with_character": {"type": "BOOLEAN"}}, ["prompt"]),
    "storyboard_frame": (_storyboard_frame, "Make the first frame of one video scene with the company's character (from the picture budget): moment = who does what and "
                         "the framing at the scene's start; outfit and setting = the video's (the same for every scene); continue_from = the media_id of the "
                         "previous scene's frame, so clothes, place and light match. Returns a media_id for the scene's start_frame.",
                         {"moment": {"type": "STRING"}, "outfit": {"type": "STRING"}, "setting": {"type": "STRING"}, "continue_from": {"type": "INTEGER"}},
                         ["moment", "outfit", "setting"]),
    "character": (_character, "The company's character as the owner created it: name, who it is, how it looks (never changes), personality, voice, the owner's rules, "
                  "and whether its pictures are there.", {}, []),
    "video_studio": (_video_studio, "The video studio: the owner's videos per day or week and how many this period has and has left, the monthly video budget and "
                     "what is left, the quality, the videos made (status, series, idea, hook, where they were posted) and concepts waiting for the owner.",
                     {"limit": {"type": "INTEGER"}}, []),
    "content_calendar": (_content_calendar, "The social media calendar: posts of the last days and the coming weeks (scheduled, posted, waiting for a video or to be posted by hand) "
                         "and post proposals waiting for the owner.", {"days_back": {"type": "INTEGER"}, "days_ahead": {"type": "INTEGER"}}, []),
    "social_results": (_social_results, "What social media achieved in the last N days: followers per network, totals per network (posts, reach, views, likes, comments, shares, "
                       "saves, clicks), the best posts, and posts waiting.", {"days": {"type": "INTEGER"}}, []),
    "marketing_plans": (_marketing_plans, "The Marketing Lead's active campaign plan (goals, audiences, campaigns, briefs, measures) and the recent ones.", {}, []),
    "product_numbers": (_product_numbers, "Every Insights number against the previous period, and page speed, over N days.", {"days": {"type": "INTEGER"}}, []),
    "health_numbers": (_health_numbers, "Open errors, alerts of the last 7 days, uptime and speed of the last 24 hours.", {}, []),
    "browse": (_browse, "Work in a real web browser for the owner, who watches live on the Nia page: goal (what to do, in the owner's words, with every "
               "detail they gave), url (where to start, optional). Only while the owner talks to you. Control Tower asks the owner before anything that sends, "
               "buys, pays, signs, accepts, deletes or signs in, and hands passwords to the owner.",
               {"goal": {"type": "STRING"}, "url": {"type": "STRING"}}, ["goal"]),
    "browser_status": (_browser_status, "How the browser session stands: status (running, asking, owner, paused, done, stopped), the question waiting "
                       "for the owner, the last steps, the report when done.", {}, []),
    "agent_templates": (_agent_templates, "What a new agent can be made of: the starter templates, the tools and actions the agent template offers, "
                        "the goal numbers and the rules. Read it before proposing a new agent (agent.create).", {}, []),
    "agent_profile": (_agent_profile, "One agent's whole picture: its job and built-in guide, its tools (and which are not ticked), the actions it may propose "
                      "with their shape, the owner's settings (schedule, model, budget, levels), its current playbook and its last runs. Read it before "
                      "writing or changing the agent's playbook or settings.", {"agent": {"type": "STRING", "description": "The agent's key, e.g. marketing_lead."}}, ["agent"]),
    "read_settings": (_read_settings, "The admin's current values of one setting (secrets hidden), e.g. brand, marketing, social, "
                      "insights, agents, qa, trust, nia. Without key: the list. Read before a change: "
                      "a PUT replaces the whole setting.", {"key": {"type": "STRING"}}, []),
    "run_agent": (_run_agent, "Start one agent's run now, only because the owner just asked for it. Its result comes later (agent_report).",
                  {"agent": {"type": "STRING", "description": "The agent's key."}}, ["agent"]),
    "ask_agent": (_ask_agent, "Put the owner's question to one agent and bring back its answer, from its own tools (it shows in that agent's chat; its "
                  "proposals wait in Approvals). Costs that agent's AI budget.",
                  {"agent": {"type": "STRING", "description": "The agent's key."}, "question": {"type": "STRING"}}, ["agent", "question"]),
    "browser_answer": (_browser_answer, "Pass the owner's answer to the browser session, only after the owner said it: allow (the step Nia asked about), "
                       "take_over, hand_back, pause, resume, stop — or reply with text (the owner's answer to Nia's question).",
                       {"decision": {"type": "STRING"}, "text": {"type": "STRING"}}, ["decision"]),
    "decide_approvals": (_decide_approvals, "Approve or reject waiting proposals the owner just told you to, after reading back exactly which ones. Never L3 (money, prices, contracts, deletions): those are confirmed on screen.",
                         {"ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}, "decision": {"type": "STRING"}, "note": {"type": "STRING"}}, ["ids", "decision"]),
    "propose": (_propose, "Propose an action for the owner to approve. Nothing happens until it is approved. Give the reason, the expected effect and the evidence.",
                {"action_type": {"type": "STRING"}, "title": {"type": "STRING"}, "payload_json": {"type": "STRING", "description": "The action's details as a JSON object."},
                 "reason": {"type": "STRING"}, "expected_effect": {"type": "STRING"}, "evidence": {"type": "ARRAY", "items": {"type": "STRING"}}},
                ["action_type", "title", "payload_json"]),
}


# Tools that work only in an agent's run (the worker), not while the owner talks to the agent in the admin. None in the
# core (one install has one database connection); a plugin may name its own.
RUN_ONLY: tuple[str, ...] = ()


# Read-only tools every agent always has (no tick needed): what really became of its own proposals.
ALWAYS = ("proposal_status",)


def usable(agent: registry.Agent, granted: list[str]) -> tuple[str, ...]:
    """The tools an agent may use in a run: in its design, granted by the owner, and built — and the ones it always has."""
    mine = tuple(t for t in registry.tools_of(agent) if t in granted and t in CATALOGUE)
    return mine + tuple(t for t in ALWAYS if t not in mine)


def declarations(names: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for name in names:
        _fn, description, properties, required = CATALOGUE[name]
        params: dict[str, Any] = {"type": "OBJECT", "properties": properties}
        if required:
            params["required"] = required
        out.append({"name": name, "description": description, "parameters": params})
    return out


def run(ctx: Context, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in ctx.granted:
        return {"error": f"No tool called {name!r} for {ctx.agent.name}."}
    fn, _description, properties, _required = CATALOGUE[name]
    clean = {k: v for k, v in (args or {}).items() if k in properties}
    try:
        return fn(ctx, **clean)
    except TypeError:
        return {"error": f"Wrong arguments for {name}."}
    except Exception as exc:  # noqa: BLE001 - a failed tool becomes a sentence for the model, never a crash
        log.warning("team tool %s failed: %s", name, exc.__class__.__name__)
        return {"error": f"{name} failed. Do not guess the result."}
