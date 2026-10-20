"""Durable task queue on the application database (ADR-001).

Why not Celery/Redis as the broker: business rows and their tasks commit in one
transaction (an application is never "queued" without a task, and a task never
points at a row that was rolled back), a restart loses nothing, and the queue is
inspectable with SQL. Redis is used *alongside* for
cross-process events and rate-limit buckets when configured.

Claiming is atomic: on PostgreSQL via `FOR UPDATE SKIP LOCKED`, on SQLite via a
conditional UPDATE that only succeeds for one worker. Retries use exponential
back-off with jitter; a task that keeps failing goes to `dead` (the dead-letter
state) and stays queryable.
"""

from __future__ import annotations

import random
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..core.errors import AppError
from ..models import AgentTask

QUEUES = (
    "agents",
    "notifications",
    "maintenance",
    "default",
)
# Queues whose tasks call an AI model: limited to MAX_CONCURRENT_AI_TASKS at a time.
AI_QUEUES = ("agents",)

WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
STALE_LOCK = timedelta(minutes=20)


def enqueue(
    session: Session,
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    queue: str = "default",
    priority: int = 5,
    run_at: datetime | None = None,
    max_attempts: int = 3,
    dedupe_key: str | None = None,
    correlation_id: str | None = None,
    entity_type: str = "",
    entity_id: str | int = "",
) -> AgentTask | None:
    """Add a task. With `dedupe_key`, an identical pending task is not duplicated."""
    if queue not in QUEUES:
        raise ValueError(f"Unknown queue {queue!r}")
    now = datetime.now(UTC)
    if dedupe_key:
        # Named columns only: the admin connection may queue server work but never read a task's payload.
        existing = session.execute(
            select(AgentTask.id, AgentTask.status, AgentTask.locked_at).where(
                AgentTask.dedupe_key == dedupe_key,
                AgentTask.status.in_(("queued", "running")),
            )
        ).first()
        if existing:
            # A "running" task whose worker died would block its own replacement
            # forever (the stale-lock reaper included). Hand it back instead.
            if existing.status == "running" and existing.locked_at and existing.locked_at < now - STALE_LOCK:
                session.execute(update(AgentTask).where(AgentTask.id == existing.id).values(
                    status="queued", locked_by="", last_error="worker lock expired", run_at=now, updated_at=now))
                return session.get(AgentTask, existing.id)
            return None
        # a finished task with the same key must not block a new one
        session.execute(
            update(AgentTask)
            .where(AgentTask.dedupe_key == dedupe_key)
            .values(dedupe_key=None)
        )
    task = AgentTask(
        queue=queue,
        task_type=task_type,
        payload=payload or {},
        status="queued",
        priority=priority,
        run_at=run_at or now,
        max_attempts=max_attempts,
        dedupe_key=dedupe_key,
        correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:12]}",
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id != "" else "",
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    return task


def claim(session: Session, queues: list[str] | tuple[str, ...], *, worker_id: str = WORKER_ID) -> AgentTask | None:
    """Atomically claim the next runnable task."""
    now = datetime.now(UTC)
    dialect = session.get_bind().dialect.name
    base = (
        select(AgentTask.id)
        .where(
            AgentTask.status == "queued",
            AgentTask.queue.in_(tuple(queues)),
            AgentTask.run_at <= now,
        )
        .order_by(AgentTask.priority.asc(), AgentTask.run_at.asc(), AgentTask.id.asc())
        .limit(1)
    )
    if dialect == "postgresql":
        base = base.with_for_update(skip_locked=True)
    for _ in range(5):
        task_id = session.execute(base).scalar_one_or_none()
        if task_id is None:
            return None
        claimed = session.execute(
            update(AgentTask)
            .where(AgentTask.id == task_id, AgentTask.status == "queued")
            .values(
                status="running",
                locked_at=now,
                locked_by=worker_id,
                started_at=now,
                attempts=AgentTask.attempts + 1,
                updated_at=now,
            )
        )
        if getattr(claimed, "rowcount", 0) == 1:
            session.flush()
            return session.get(AgentTask, task_id)
    return None


def backoff_delay(attempt: int, *, base: float = 15.0, cap: float = 3600.0) -> float:
    """Exponential back-off with full jitter."""
    return random.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))  # noqa: S311 - not security


def complete(session: Session, task: AgentTask, result: dict[str, Any] | None = None) -> None:
    now = datetime.now(UTC)
    task.status = "succeeded"
    task.result = result or {}
    task.finished_at = now
    task.locked_by = ""
    task.updated_at = now


def fail(session: Session, task: AgentTask, error: Exception) -> str:
    """Record a failure. Returns the resulting status: queued (retry) | dead | failed."""
    now = datetime.now(UTC)
    message = f"{error.__class__.__name__}: {error}"[:2000]
    code = getattr(error, "code", error.__class__.__name__)
    retryable = bool(getattr(error, "retryable", not isinstance(error, AppError)))
    task.last_error = message
    task.last_error_code = str(code)
    task.locked_by = ""
    task.updated_at = now
    if retryable and task.attempts < task.max_attempts:
        task.status = "queued"
        task.run_at = now + timedelta(seconds=backoff_delay(task.attempts))
        return "queued"
    task.status = "dead" if retryable else "failed"
    task.finished_at = now
    return task.status


def requeue_stale(session: Session, *, older_than: timedelta = STALE_LOCK, locked_by_prefix: str | None = None) -> int:
    """Tasks locked by a worker that died come back to the queue (retry-safe).

    With `locked_by_prefix` (a hostname), only that host's tasks are reclaimed —
    what a worker does at start-up for its own predecessor."""
    cutoff = datetime.now(UTC) - older_than
    stmt = update(AgentTask).where(AgentTask.status == "running", AgentTask.locked_at < cutoff)
    if locked_by_prefix:
        stmt = stmt.where(AgentTask.locked_by.like(f"{locked_by_prefix}:%"))
    result = session.execute(stmt.values(status="queued", locked_by="", last_error="worker lock expired", updated_at=datetime.now(UTC)))
    return int(getattr(result, "rowcount", 0) or 0)


def retry_task(session: Session, task: AgentTask) -> None:
    now = datetime.now(UTC)
    task.status = "queued"
    task.run_at = now
    task.attempts = 0
    task.locked_by = ""
    task.finished_at = None
    task.updated_at = now


def cancel_task(session: Session, task: AgentTask) -> None:
    now = datetime.now(UTC)
    if task.status in ("queued", "running"):
        task.status = "cancelled"
        task.finished_at = now
        task.updated_at = now


def queue_depths(session: Session) -> dict[str, dict[str, int]]:
    rows = session.execute(
        select(AgentTask.queue, AgentTask.status, func.count(AgentTask.id)).group_by(
            AgentTask.queue, AgentTask.status
        )
    ).all()
    depths: dict[str, dict[str, int]] = {q: {} for q in QUEUES}
    for queue, status, count in rows:
        depths.setdefault(queue, {})[status] = int(count)
    return depths
