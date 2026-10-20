"""The trust view of what waits: the runs that delete data on time, and every proposal waiting for the owner's
clearance longer than the owner's rules allow. Read by agents (their tools) and by Admin → Trust."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import AgentTask, ScheduledJob, TeamApproval
from . import rules as trust_rules

# Runs that delete data on time: a run that is late or failing is a compliance problem.
DELETION_RUNS = ("mail_purge", "purge_finished_tasks", "insights_prune")


def _days(since: datetime | None, now: datetime) -> int | None:
    return max(0, (now - since).days) if since else None


def deadlines(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """The data-deletion runs: when each last finished and whether it is on time."""
    now = now or datetime.now(UTC)
    runs = []
    for job in session.execute(select(ScheduledJob).where(ScheduledJob.name.in_(DELETION_RUNS))).scalars():
        # The schedule only says when the run was queued; how it went is the task's own outcome.
        last = session.execute(select(AgentTask.status, AgentTask.finished_at, AgentTask.last_error).where(AgentTask.task_type == job.task_type)
                               .order_by(AgentTask.id.desc()).limit(1)).one_or_none()
        finished = last.finished_at if last is not None else None
        late = finished is None or now - finished > timedelta(seconds=job.interval_seconds * 2)
        failed = last is not None and last.status in ("failed", "dead")
        runs.append({"name": job.name, "enabled": job.enabled, "last_run": finished.isoformat() if finished else None, "last_status": last.status if last else "never",
                     "last_error": ((last.last_error or "") if failed and last is not None else "")[:300], "ok": job.enabled and not late and not failed})
    return {"deletion_runs": runs}


def overdue(session: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Proposals waiting for clearance longer than the owner's rule (nothing when the owner set no rule)."""
    now = now or datetime.now(UTC)
    the_rules = trust_rules.get(session)
    out: list[dict[str, Any]] = []
    if the_rules.wait_alert_days is not None:
        limit = now - timedelta(days=the_rules.wait_alert_days)
        for row in session.execute(select(TeamApproval).where(TeamApproval.status == "pending", TeamApproval.created_at < limit)
                                   .order_by(TeamApproval.created_at)).scalars():
            out.append({"what": "proposal waiting for clearance", "id": row.id, "name": row.title, "agent": row.agent, "days": _days(row.created_at, now)})
    return out


def overview(session: Session) -> dict[str, Any]:
    """Admin → Trust: everything at once."""
    return {"deadlines": deadlines(session), "overdue": overdue(session), "rules": trust_rules.get(session).__dict__}
