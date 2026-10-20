"""the HQ: the agent team as people in a 3D office, live. This is the state the office
is drawn from: who is at their desk working, who is idle, who is switched off, which agent walked over to a colleague
with a handoff, and which agent the owner is talking to (the owner then stands in that agent's office).

Read from what the team already records (runs, events, conversations); nothing is stored for the office. How long
a handoff or a conversation keeps someone in another office is a display choice, not a business value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import (
    NiaConversation,
    NiaMessage,
    TeamApproval,
    TeamConversation,
    TeamEvent,
    TeamMessage,
    TeamRun,
)
from ..nia import memory as nia_memory
from ..team import config, costs, registry

VISIT = timedelta(minutes=8)  # how long an agent stays in a colleague's office after a handoff
TALK = timedelta(minutes=1)  # the owner leaves an agent's office a minute after the conversation went quiet (owner's choice)
STUCK = timedelta(minutes=45)  # a run "running" longer than this is not shown as working (it was cut off)

# What an agent is doing, said the way a colleague would see it (tools not named here read "Using <tool>").
DOING = {
    "web_search": "Searching the web", "read_web_page": "Reading a web page", "check_company": "Checking a company's website",
    "hiring_employers": "Looking at who is hiring", "employer_jobs": "Reading job adverts", "list_companies": "Going through the company list",
    "read_company": "Reading a company's file", "propose": "Writing a proposal for you", "keep_note": "Writing a note",
    "emit_event": "Handing work to a colleague", "read_insights": "Reading the numbers", "read_events": "Catching up on handoffs",
    "search_brain": "Asking the team's brain", "letters_due": "Picking companies for letters", "letter_templates": "Reading the letter templates",
    "list_inbox": "Reading the mailbox", "read_mail": "Reading an e-mail", "sort_mail": "Sorting the mailbox", "list_bookings": "Checking booked calls",
    "save_brief": "Writing a call brief", "company_history": "Reading a company's history", "offer_facts": "Checking the offer",
    "create_image": "Designing an image", "storyboard_frame": "Drawing a storyboard", "content_calendar": "Planning posts",
    "social_results": "Reading social results", "run_checks": "Testing the platform", "ops_signals": "Watching the servers",
    "team_overview": "Looking over the team", "agent_report": "Reading a colleague's report",
}


def _doing(calls: list[dict[str, Any]]) -> str:
    if not calls:
        return "Getting started"
    name = str(calls[-1].get("tool", ""))
    return DOING.get(name, f"Using {name.replace('_', ' ')}")


def _first_line(text: str, limit: int = 160) -> str:
    """The first line of a run's summary that says something (not a heading like "What I looked at:")."""
    lines = [x.strip(" -•*#") for x in (text or "").splitlines() if x.strip(" -•*#")]
    line = next((x for x in lines if not x.endswith(":")), lines[0] if lines else "")
    return line[:limit] + ("…" if len(line) > limit else "")


def state(session: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    running: dict[str, TeamRun] = {}
    run: TeamRun | None
    for run in session.execute(select(TeamRun).where(TeamRun.status == "running", TeamRun.started_at >= now - STUCK).order_by(TeamRun.id)).scalars():
        running[run.agent] = run
    last: dict[str, TeamRun] = {}
    last_ids = select(TeamRun.agent, func.max(TeamRun.id).label("id")).where(TeamRun.status.in_(("done", "failed"))).group_by(TeamRun.agent).subquery()
    for run in session.execute(select(TeamRun).join(last_ids, TeamRun.id == last_ids.c.id)).scalars():
        last[run.agent] = run
    runs_today: dict[str, int] = {str(k): int(v) for k, v in session.execute(select(TeamRun.agent, func.count()).where(TeamRun.started_at >= day).group_by(TeamRun.agent)).all()}
    proposals_today: dict[str, int] = {str(k): int(v) for k, v in session.execute(select(TeamApproval.agent, func.count()).where(TeamApproval.created_at >= day).group_by(TeamApproval.agent)).all()}
    # Everything each agent's AI work cost today: runs, chats, searches, its pictures and videos (services/team/costs.py).
    cost_today = costs.by_agent(session, list(registry.AGENTS), day)
    waiting_for_owner: dict[str, int] = {str(k): int(v) for k, v in session.execute(select(TeamApproval.agent, func.count()).where(TeamApproval.status == "pending").group_by(TeamApproval.agent)).all()}

    # Handoffs: the agent that sent one walks over to the first colleague it wakes.
    subscribers: dict[str, list[str]] = {}
    for agent in registry.AGENTS.values():
        for type_ in agent.subscribes:
            subscribers.setdefault(type_, []).append(agent.key)
    visits: dict[str, dict[str, Any]] = {}
    handoffs: list[dict[str, Any]] = []
    for e in session.execute(select(TeamEvent).where(TeamEvent.created_at >= now - VISIT * 6).order_by(TeamEvent.id.desc()).limit(40)).scalars():
        to = [k for k in subscribers.get(e.type, []) if k != e.source]
        if e.source not in registry.AGENTS:
            continue
        handoffs.append({"by": e.source, "to": to, "type": e.type, "at": e.created_at.isoformat()})
        if to and e.source not in visits and e.created_at >= now - VISIT:
            visits[e.source] = {"to": to[0], "about": e.type, "at": e.created_at.isoformat()}

    # The owner: in the office of the agent whose conversation had a message (the owner's or the answer) in the last minute, else in their own.
    talks: list[tuple[datetime, str]] = []
    team_last = session.execute(select(TeamMessage.created_at, TeamConversation.agent).join(TeamConversation, TeamConversation.id == TeamMessage.conversation_id)
                                .where(TeamMessage.created_at >= now - TALK).order_by(TeamMessage.id.desc()).limit(1)).first()
    if team_last:
        talks.append((team_last[0], team_last[1]))
    nia_last = session.execute(select(NiaMessage.created_at).join(NiaConversation, NiaConversation.id == NiaMessage.conversation_id)
                               .where(NiaMessage.created_at >= now - TALK).order_by(NiaMessage.id.desc()).limit(1)).scalar()
    if nia_last:
        talks.append((nia_last, "nia"))
    talks.sort(reverse=True)
    with_agent = talks[0][1] if talks else None

    agents = []
    for agent in registry.AGENTS.values():
        status, waiting = config.status(session, agent.key)
        run = running.get(agent.key)
        done = last.get(agent.key)
        if run is not None:
            mode, doing = "working", _doing(run.tool_calls or [])
        elif status == "ready":
            mode, doing = "idle", (_first_line(done.summary) if done and done.summary else "At the desk")
        elif status == "waiting":
            mode, doing = "waiting", "Waiting for " + ", ".join(waiting)
        else:
            mode, doing = "off", "Switched off"
        agents.append({
            "key": agent.key, "name": agent.name, "department": agent.department, "purpose": agent.purpose, "mode": mode, "doing": doing,
            "trigger": run.trigger if run else None, "since": run.started_at.isoformat() if run else None,
            "last_run_at": (done.finished_at or done.started_at).isoformat() if done else None,
            "visiting": visits.get(agent.key) if mode != "off" else None,
            "runs_today": int(runs_today.get(agent.key, 0)), "proposals_today": int(proposals_today.get(agent.key, 0)),
            "cost_today_usd": round(float(cost_today.get(agent.key, 0.0)), 4), "waiting_for_owner": int(waiting_for_owner.get(agent.key, 0)),
        })
    # Outside agents at their desks in the outside wing: working while they call the gateway or run, else at the desk.
    from ...models import OutsideAgent

    for o in session.execute(select(OutsideAgent).where(OutsideAgent.status != "revoked").order_by(OutsideAgent.id)).scalars():
        run = running.get(o.key)
        done = last.get(o.key)
        active = o.last_seen_at is not None and o.last_seen_at >= now - timedelta(minutes=5)
        if o.status == "paused":
            mode, doing = "off", "Paused by you"
        elif run is not None or active:
            mode, doing = "working", _doing(run.tool_calls or []) if run else "Working through the gateway"
        elif o.status in ("draft", "waiting"):
            mode, doing = "waiting", "Waiting for its credential" if o.status == "draft" else "Waiting to connect"
        else:
            mode, doing = "idle", (_first_line(done.summary) if done and done.summary else "At the desk")
        label = o.client_label or {"anthropic": "Claude", "openai": "ChatGPT", "google": "Gemini"}.get(o.provider, "outside")
        agents.append({
            "key": o.key, "name": o.name, "department": "Outside", "purpose": o.job[:300], "mode": mode, "doing": doing,
            "trigger": run.trigger if run else None, "since": run.started_at.isoformat() if run else None,
            "last_run_at": (done.finished_at or done.started_at).isoformat() if done else None, "visiting": None,
            "runs_today": int(runs_today.get(o.key, 0)), "proposals_today": int(proposals_today.get(o.key, 0)), "cost_today_usd": 0.0,
            "waiting_for_owner": int(waiting_for_owner.get(o.key, 0)), "desk": o.desk, "outside": label,
        })
    owner = nia_memory.owner(session)
    return {
        "now": now.isoformat(),
        "owner": {"name": owner.get("address_as") or owner.get("name") or "", "with_agent": with_agent},
        "agents": agents,
        "handoffs": handoffs[:20],
    }
