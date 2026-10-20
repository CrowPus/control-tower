"""What the Engineer on Duty reads about the system itself, beyond requests and uptime:
background tasks that failed or died, the queue and the scheduler, e-mails and phone notifications that did not
arrive, certificates, the database and the disk. Facts and counts only; no member content (the task list shows
type, status and the error line, never a payload)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ...models import AgentTask, EmailDelivery, QaRun, ScheduledJob

# A task locked longer than this is reported as stuck; scheduler jobs this many intervals late as overdue.
STUCK = timedelta(minutes=30)
LATE_INTERVALS = 3


def _line(error: str) -> str:
    return " ".join((error or "").split())[:240]


def tasks(session: Session, hours: int = 24) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    now = datetime.now(UTC)
    failed: list[dict[str, Any]] = []
    for task_type, status, n, last in session.execute(select(AgentTask.task_type, AgentTask.status, func.count(), func.max(AgentTask.finished_at)).where(
            AgentTask.status.in_(("failed", "dead")), func.coalesce(AgentTask.finished_at, AgentTask.run_at) >= since).group_by(AgentTask.task_type, AgentTask.status)).tuples():
        error = session.execute(select(AgentTask.last_error, AgentTask.last_error_code).where(AgentTask.task_type == task_type, AgentTask.status == status)
                                .order_by(AgentTask.id.desc()).limit(1)).first()
        failed.append({"task": task_type, "status": status, "count": int(n), "last_at": last.isoformat() if last else None,
                       "last_error": _line(error.last_error) if error else "", "code": error.last_error_code if error else ""})
    failed.sort(key=lambda f: -f["count"])
    waiting = session.execute(select(AgentTask.queue, func.count(), func.min(AgentTask.run_at)).where(AgentTask.status == "queued", AgentTask.run_at <= now)
                              .group_by(AgentTask.queue)).tuples().all()
    stuck = session.execute(select(AgentTask.task_type, func.count()).where(AgentTask.status == "running", AgentTask.locked_at < now - STUCK)
                            .group_by(AgentTask.task_type)).tuples().all()
    return {"hours": hours, "failed": failed,
            "queue": [{"queue": q, "waiting": int(n), "oldest_waiting_minutes": int((now - oldest).total_seconds() // 60) if oldest else 0} for q, n, oldest in waiting],
            "stuck": [{"task": t, "count": int(n)} for t, n in stuck]}


def scheduler(session: Session) -> list[dict[str, Any]]:
    """Scheduled jobs that are late (the scheduler or the worker may be down) or whose last run failed."""
    now = datetime.now(UTC)
    out = []
    for job in session.execute(select(ScheduledJob).where(ScheduledJob.enabled.is_(True))).scalars():
        late = now - job.next_run_at
        if late > timedelta(seconds=max(job.interval_seconds, 60) * LATE_INTERVALS) or job.last_error:
            out.append({"job": job.name, "task": job.task_type, "late_minutes": max(0, int(late.total_seconds() // 60)), "last_status": job.last_status,
                        "last_error": _line(job.last_error), "last_run": job.last_run_at.isoformat() if job.last_run_at else None})
    return out


def deliveries(session: Session, hours: int = 24) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    mails = session.execute(select(EmailDelivery.kind, EmailDelivery.status, func.count(), func.max(EmailDelivery.last_error)).where(
        EmailDelivery.created_at >= since, EmailDelivery.status.in_(("failed", "bounced", "complained", "retrying"))).group_by(EmailDelivery.kind, EmailDelivery.status)).tuples().all()
    sent = session.execute(select(func.count()).select_from(EmailDelivery).where(EmailDelivery.created_at >= since)).scalar() or 0
    return {"emails_24h": int(sent), "emails_not_delivered": [{"kind": k, "status": s, "count": int(n), "error": e or ""} for k, s, n, e in mails]}


def certificates(session: Session) -> list[dict[str, Any]]:
    row = session.execute(select(QaRun).order_by(QaRun.started_at.desc()).limit(1)).scalar_one_or_none()
    return [{"host": c["name"].split(": ", 1)[1], "ok": c["ok"], "detail": c.get("detail", "")} for c in (row.checks if row else []) if c["name"].startswith("Certificate: ")]


def storage(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        usage = shutil.disk_usage("/")
        out["disk"] = {"total_gb": round(usage.total / 1e9, 1), "free_gb": round(usage.free / 1e9, 1), "used_share": round(usage.used / usage.total, 3)}
    except OSError:
        out["disk"] = None
    if session.get_bind().dialect.name == "postgresql":
        try:
            with session.begin_nested():
                size = session.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
                top = session.execute(text("SELECT relname, pg_total_relation_size(relid) FROM pg_catalog.pg_statio_user_tables ORDER BY 2 DESC LIMIT 5")).all()
            out["database_gb"] = round(int(size) / 1e9, 2)
            out["largest_tables"] = [{"table": t, "mb": round(int(b) / 1e6, 1)} for t, b in top]
        except DBAPIError:
            out["database_gb"] = None
    return out


def signals(session: Session) -> dict[str, Any]:
    from ..insights import queries

    return {"tasks": tasks(session), "scheduler_late_or_failing": scheduler(session), "deliveries": deliveries(session), "certificates": certificates(session),
            "storage": storage(session), "open_errors": queries.errors(session, "open", 20), "alerts_7_days": queries.alerts(session, 7),
            "uptime_24h": queries.uptime(session, datetime.now(UTC) - timedelta(days=1)), "slowest_routes_24h": queries.performance(session, 24)["slowest"]}
