"""Task handler registry. Importing this module registers every handler.

Domain handlers live next to their domain and are pulled in here so the worker
process and the embedded worker see the same set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from ..core.events import bus
from ..core.logging import get_logger, log_event
from ..db import engine, transaction
from ..models import AgentTask, LoginAttempt

# Each of these modules registers its own @task handlers on import.
from ..services.insights import (
    tasks as _insights_tasks,  # noqa: F401 - registers the insights.* tasks
)
from ..services.marketing import plans as _marketing_plans  # noqa: F401 - registers marketing.plan
from ..services.marketing import (
    tasks as _marketing_tasks,  # noqa: F401 - registers marketing.video_step
)
from ..services.marketing import (
    video_studio as _video_studio,  # noqa: F401 - registers video.concept
)
from ..services.nia import (
    browser as _nia_browser,  # noqa: F401 - registers nia.browse_step
)
from ..services.nia import (
    journal as _nia_journal,  # noqa: F401 - registers nia.journal
)
from ..services.outside import (
    hosted as _outside_hosted,  # noqa: F401 - registers outside.run, outside.dispatch
)
from ..services.qa import (
    tasks as _qa_tasks,  # noqa: F401 - registers qa.sweep
)
from ..services.social import (
    tasks as _social_tasks,  # noqa: F401 - registers social.publish, social.due, social.metrics
)
from ..services.team import (
    tasks as _team_tasks,  # noqa: F401 - registers team.dispatch and team.run
)
from ..services.webmail import (
    tasks as _webmail_tasks,  # noqa: F401 - registers mail.sync_all, mail.sync, mail.send
)
from . import queue as q
from .worker import task

log = get_logger(__name__)

# Sign-in attempts are kept this long for throttling, then deleted.
LOGIN_ATTEMPTS_KEPT = timedelta(days=2)
# Finished tasks stay visible in Admin → Task queue this long.
FINISHED_TASKS_KEPT = timedelta(days=14)


# --- maintenance -----------------------------------------------------------------------------
@task("maintenance.requeue_stale", queue="maintenance")
def maintenance_requeue_stale(_job: AgentTask) -> dict[str, Any]:
    with transaction(write=True) as session:
        count = q.requeue_stale(session)
        purged = session.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < datetime.now(UTC) - LOGIN_ATTEMPTS_KEPT))
    return {"requeued": count, "login_attempts_purged": int(getattr(purged, "rowcount", 0) or 0)}


@task("maintenance.health_check", queue="maintenance")
def maintenance_health_check(_job: AgentTask) -> dict[str, Any]:
    from sqlalchemy import text

    with engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    with transaction() as session:
        depths = q.queue_depths(session)
    dead = sum(statuses.get("dead", 0) for statuses in depths.values())
    if dead:
        bus.emit("worker.dead_letter", count=dead)
    return {"database": "ok", "dead_letter": dead}


# --- e-mail delivery (services/mail_delivery.py, mail_bounces.py) ---------------------------
@task("mail.retry", queue="notifications")
def mail_retry(_job: AgentTask) -> dict[str, Any]:
    """Every minute: send again the e-mails a busy mail server could not take."""
    from ..services import mail_delivery

    with transaction(write=True) as session:
        return mail_delivery.retry_due(session)


@task("mail.bounces", queue="notifications")
def mail_bounces(_job: AgentTask) -> dict[str, Any]:
    """Every five minutes: which e-mails came back, and why."""
    from ..services import mail_bounces as bounces

    with transaction(write=True) as session:
        return dict(bounces.scan(session))


@task("mail.purge", queue="maintenance")
def mail_purge(_job: AgentTask) -> dict[str, Any]:
    """Daily: the record of an e-mail is kept 90 days."""
    from ..services import mail_delivery

    with transaction(write=True) as session:
        return {"purged": mail_delivery.purge(session)}


@task("maintenance.purge_finished_tasks", queue="maintenance")
def maintenance_purge_tasks(_job: AgentTask) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - FINISHED_TASKS_KEPT
    with transaction(write=True) as session:
        rows = session.execute(select(AgentTask).where(AgentTask.status.in_(("succeeded", "cancelled")), AgentTask.finished_at < cutoff)).scalars().all()
        for row in rows:
            session.delete(row)
    log_event(log, "purged finished tasks", count=len(rows))
    return {"purged": len(rows)}
