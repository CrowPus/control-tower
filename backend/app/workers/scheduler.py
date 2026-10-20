"""Periodic jobs: the agents, retries, cleanup, health.

The scheduler only *enqueues* tasks when `next_run_at` is due; the worker runs
them. Definitions live in the database (`scheduled_jobs`), with these defaults
seeded on first boot.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..core.logging import get_logger, log_event
from ..db import transaction
from ..models import ScheduledJob
from . import queue as q

log = get_logger(__name__)

DEFAULT_JOBS: list[dict[str, Any]] = [
    {"name": "requeue_stale_tasks", "task_type": "maintenance.requeue_stale", "queue": "maintenance", "interval_seconds": 10 * 60},
    {"name": "health", "task_type": "maintenance.health_check", "queue": "maintenance", "interval_seconds": 5 * 60},
    {"name": "purge_finished_tasks", "task_type": "maintenance.purge_finished_tasks", "queue": "maintenance", "interval_seconds": 24 * 3600},
    # Outside agents: hosted ones start on the owner's schedule.
    {"name": "outside_dispatch", "task_type": "outside.dispatch", "queue": "maintenance", "interval_seconds": 60},
    # E-mail: retry what a busy mail server could not take; read returned e-mails; keep the record 90 days.
    {"name": "mail_retry", "task_type": "mail.retry", "queue": "notifications", "interval_seconds": 60},
    {"name": "mail_bounces", "task_type": "mail.bounces", "queue": "notifications", "interval_seconds": 5 * 60},
    {"name": "mail_purge", "task_type": "mail.purge", "queue": "maintenance", "interval_seconds": 24 * 3600},
    # Insights: numbers, uptime, alerts, Monday report, housekeeping.
    {"name": "insights_snapshot", "task_type": "insights.snapshot", "queue": "maintenance", "interval_seconds": 15 * 60},
    {"name": "insights_uptime", "task_type": "insights.uptime", "queue": "maintenance", "interval_seconds": 60},
    {"name": "insights_alerts", "task_type": "insights.alerts", "queue": "maintenance", "interval_seconds": 5 * 60},
    {"name": "insights_weekly", "task_type": "insights.weekly", "queue": "notifications", "interval_seconds": 3600},
    {"name": "insights_prune", "task_type": "insights.prune", "queue": "maintenance", "interval_seconds": 3600},
    # The agents: start due agents, carry out cleared actions. Idle until the owner creates and switches on an agent.
    {"name": "team_dispatch", "task_type": "team.dispatch", "queue": "maintenance", "interval_seconds": 60},
    # Meetings with Nia: decks prepared at the owner's time, minutes after, the next weekly one.
    {"name": "nia_meetings", "task_type": "nia.meetings", "queue": "default", "interval_seconds": 5 * 60},
    # Nia's journal: quiet conversations written down as memories.
    {"name": "nia_journal", "task_type": "nia.journal", "queue": "default", "interval_seconds": 10 * 60},
    # Mail: every account mirrored each minute; mails of the outbox whose task was lost.
    {"name": "mail_sync", "task_type": "mail.sync_all", "queue": "maintenance", "interval_seconds": 60},
    # Social media: scheduled posts whose task was lost; what posts achieved.
    {"name": "social_due", "task_type": "social.due", "queue": "maintenance", "interval_seconds": 60},
    {"name": "social_metrics", "task_type": "social.metrics", "queue": "maintenance", "interval_seconds": 3600},
    # QA: the checks at the owner's interval (no AI), the system's own signals.
    {"name": "qa_sweep", "task_type": "qa.sweep", "queue": "maintenance", "interval_seconds": 5 * 60},
]


def seed_default_jobs() -> None:
    now = datetime.now(UTC)
    with transaction(write=True) as session:
        existing = {job.name for job in session.execute(select(ScheduledJob)).scalars()}
        for spec in DEFAULT_JOBS:
            if spec["name"] in existing:
                continue
            session.add(
                ScheduledJob(
                    name=spec["name"],
                    task_type=spec["task_type"],
                    queue=spec["queue"],
                    interval_seconds=spec["interval_seconds"],
                    enabled=True,
                    # stagger first runs so a fresh boot does not fire everything at once
                    next_run_at=now + timedelta(seconds=30 + 20 * len(existing)),
                    created_at=now,
                    updated_at=now,
                )
            )
            existing.add(spec["name"])


def tick() -> int:
    """Enqueue every due job. Returns the number of tasks enqueued."""
    now = datetime.now(UTC)
    enqueued = 0
    with transaction(write=True) as session:
        due = session.execute(
            select(ScheduledJob).where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
        ).scalars().all()
        for job in due:
            created = q.enqueue(
                session,
                job.task_type,
                dict(job.payload or {}),
                queue=job.queue,
                dedupe_key=f"scheduled:{job.name}",
                max_attempts=2,
                entity_type="scheduled_job",
                entity_id=job.id,
            )
            job.last_run_at = now
            job.next_run_at = now + timedelta(seconds=max(30, job.interval_seconds))
            job.last_status = "enqueued" if created else "skipped_duplicate"
            job.updated_at = now
            if created:
                enqueued += 1
    return enqueued


class Scheduler:
    def __init__(self, interval: float = 15.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick_at: datetime | None = None

    def start(self) -> None:
        seed_default_jobs()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log_event(log, "scheduler started", interval=self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = tick()
                self.last_tick_at = datetime.now(UTC)
                if count:
                    log_event(log, "scheduler enqueued", count=count)
            except Exception as exc:  # noqa: BLE001
                log.error("scheduler tick failed: %s", exc.__class__.__name__, exc_info=True)
            self._stop.wait(self.interval)
