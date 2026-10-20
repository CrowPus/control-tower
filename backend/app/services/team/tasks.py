"""The worker's part of the agent team: every minute, start the agents that are due (by the
owner's schedule, or because events wait for them) and carry out approved actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ...db import transaction
from ...models import AgentTask
from ...workers import queue as q
from ...workers.worker import task
from ..marketing import plans as _marketing_plans  # noqa: F401 - registers marketing.plan
from ..marketing import video_studio as _video_studio  # noqa: F401 - registers video.concept
from ..nia import meetings
from ..product import ideas as _ideas  # noqa: F401 - registers product.idea
from ..social import posts as _social_posts  # noqa: F401 - registers social.post
from . import approvals, config, events, registry, runner


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


def start(session: Session, key: str, trigger: str) -> bool:
    """Queue a run (one at a time per agent). False when one is already waiting or running."""
    return q.enqueue(session, "team.run", {"agent": key, "trigger": trigger}, queue="agents", max_attempts=1,
                     dedupe_key=f"team.run:{key}", entity_type="team_agent", entity_id=key) is not None


# Work that waits for an agent whether or not an event is new (a run that failed leaves it waiting): the agent
# is started for it, at most once per RETRY_MINUTES (a safety rail, not a business value).
RETRY_MINUTES = 60


# None in the core: agents start by their schedule or their events; a plugin may add waiting work.
WAITING_WORK: dict[str, Callable[[Session, datetime], bool]] = {}


def _work_waits(session: Session, key: str, last_run: datetime | None, now: datetime) -> bool:
    check = WAITING_WORK.get(key)
    if check is None or (last_run is not None and now - last_run < timedelta(minutes=RETRY_MINUTES)):
        return False
    return check(session, now)


def dispatch(session: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    # Before anything else: waiting proposals that became duplicates are withdrawn.
    withdrawn = approvals.withdraw_stale(session)
    if not config.team(session).enabled:
        return {"team": "off", **({"withdrawn": withdrawn} if withdrawn else {})}
    started: list[str] = []
    for key in registry.AGENTS:
        if config.status(session, key)[0] != "ready":
            continue
        state = events.state(session, key)
        if state.last_scheduled_at is None:
            # The schedule counts from the moment the agent is first ready, not from the past.
            state.last_scheduled_at = now
            scheduled = False
        else:
            scheduled = runner.due(config.agent(session, key).schedule, state.last_scheduled_at, now)
        if scheduled:
            state.last_scheduled_at = now
        waiting = bool(events.unseen(session, key, 1)[0])
        work = not scheduled and not waiting and _work_waits(session, key, state.last_run_at, now)
        if (scheduled or waiting or work) and start(session, key, "schedule" if scheduled else "event" if waiting else "work"):
            started.append(key)
    return {"started": started, "executed": approvals.execute_due(session), **({"withdrawn": withdrawn} if withdrawn else {})}


@task("team.dispatch", queue="maintenance")
def team_dispatch(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        return dispatch(session)


@task("team.run", queue="agents")
def team_run(job: AgentTask) -> dict[str, Any]:
    key = str(job.payload.get("agent") or "")
    outcome = runner.run(_db, key, trigger=str(job.payload.get("trigger") or "manual"))
    return {"run": outcome.run_id, "status": outcome.status}


@task("nia.meetings", queue="default")
def nia_meetings(_job: AgentTask) -> dict[str, Any]:
    """Prepare decks that are due, close meetings that are over (minutes), schedule the next weekly one."""
    return meetings.tick(_db)


@task("nia.prepare", queue="default")
def nia_prepare(job: AgentTask) -> dict[str, Any]:
    """The owner asked Nia to prepare one meeting's deck now."""
    return meetings.prepare(_db, int(job.payload.get("meeting_id") or 0))
