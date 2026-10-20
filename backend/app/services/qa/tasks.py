"""The checks on the owner's schedule: no AI involved. Every 5 minutes the worker looks
whether the owner's interval has passed; if so it runs the checks, keeps the run, and tells the team when a check
broke (after the owner's failed runs in a row: `qa.failed`, which wakes the Engineer on Duty) or recovered
(`qa.recovered`)."""

from __future__ import annotations

from typing import Any

from ...db import transaction
from ...models import AgentTask
from ...workers.worker import task
from . import checks


def sweep(trigger: str = "schedule") -> dict[str, Any]:
    from ..team import events

    with transaction(write=True) as session:
        results = checks.run(session)
        row = checks.record(session, results, trigger)
        broken, recovered = checks.changes(session)
        if broken:
            events.emit(session, "qa.failed", "qa", {"run_id": row.id, "checks": broken})
        if recovered:
            events.emit(session, "qa.recovered", "qa", {"run_id": row.id, "checks": recovered})
        return {"run_id": row.id, "total": row.total, "failed": row.failed, "broken": [b["name"] for b in broken], "recovered": recovered}


@task("qa.sweep", queue="maintenance")
def qa_sweep(_job: AgentTask) -> dict[str, Any]:
    with transaction() as session:
        if not checks.due(session):
            return {"skipped": "not due"}
    return sweep("schedule")
