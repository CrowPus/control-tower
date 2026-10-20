"""The worker's part of Insights."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ...core import clock
from ...db import transaction
from ...models import AgentTask
from ...workers.worker import task
from . import alerts, maintenance, snapshot, weekly
from .collector import local_day


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@task("insights.snapshot", queue="maintenance")
def insights_snapshot(_job: AgentTask) -> dict[str, Any]:
    """Every 15 minutes: today's numbers (and yesterday's once more after midnight); the first
    run rebuilds the history from existing data."""
    now = datetime.now(UTC)
    out: dict[str, Any] = {}
    with _db() as session:
        if snapshot.days_with_data(session) == 0:
            out["backfilled_days"] = snapshot.backfill(session)
        out["today"] = snapshot.run(session, local_day(now))
        if now.astimezone(clock.zone()).hour < 3:
            out["yesterday"] = snapshot.run(session, local_day(now) - timedelta(days=1))
    return out


@task("insights.uptime", queue="maintenance")
def insights_uptime(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        return {"checked": maintenance.check_uptime(session)}


@task("insights.alerts", queue="maintenance")
def insights_alerts(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        return alerts.sync(session, alerts.evaluate(session))


@task("insights.weekly", queue="notifications")
def insights_weekly(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        week_end = weekly.due(session, datetime.now(UTC))
        return {"sent": weekly.send(session, week_end) if week_end else False}


@task("insights.prune", queue="maintenance")
def insights_prune(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        return maintenance.prune(session)
