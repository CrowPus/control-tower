"""Worker loop: claims tasks, runs registered handlers, records outcomes.

Runs embedded in the API process during development (`EMBEDDED_WORKER=true`) and
as a separate process in production: `python -m app.worker`.

Concurrency is controlled per queue (spec §43): AI-heavy queues are limited to
MAX_CONCURRENT_AI_TASKS. The platform's kill switches are consulted *before every task*.
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import settings
from ..core import logging as logctx
from ..core.errors import AppError, AutomationPaused
from ..core.events import bus
from ..core.logging import get_logger, log_event
from ..db import transaction
from ..models import AgentTask
from ..services import platform
from . import queue as q

log = get_logger(__name__)

# Touched by every polling thread; `app.worker --check` compares its age.
from ..heartbeat import HEARTBEAT  # noqa: E402 - touched by every polling thread

Handler = Callable[[AgentTask], dict[str, Any] | None]

_HANDLERS: dict[str, Handler] = {}
_QUEUE_OF: dict[str, str] = {}


def task(task_type: str, *, queue: str = "default") -> Callable[[Handler], Handler]:
    """Register a task handler. Handlers run synchronously in a worker thread and
    get a fresh DB transaction per task via `transaction()`."""

    def decorator(fn: Handler) -> Handler:
        _HANDLERS[task_type] = fn
        _QUEUE_OF[task_type] = queue
        return fn

    return decorator


def handlers() -> dict[str, Handler]:
    return dict(_HANDLERS)


# --- kill switches -----------------------------------------------------------------
def check_switches(task_queue: str) -> None:
    """Raise AutomationPaused when a platform switch (admin) forbids this queue right now."""
    with transaction() as session:
        switches = platform.switches(session)
    if switches.get("paused_all"):
        raise AutomationPaused("All automation is paused.")


class Worker:
    def __init__(self, queues: list[str] | None = None) -> None:
        cfg = settings()
        self.queues = queues or list(q.QUEUES)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._ai_limit = threading.BoundedSemaphore(max(1, cfg.max_concurrent_ai_tasks))
        self.concurrency = max(1, cfg.worker_concurrency)
        self.poll_interval = cfg.worker_poll_interval_seconds
        self.processed = 0
        self.failed = 0
        self.current: dict[str, str] = {}
        self.started_at = datetime.now(UTC)

    # --- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        # Whatever a previous worker on this host left "running" is orphaned now
        # (a restart mid-task); give it back to the queue before claiming anything.
        try:
            with transaction(write=True) as session:
                reclaimed = q.requeue_stale(session, older_than=timedelta(seconds=0), locked_by_prefix=q.WORKER_ID.split(":")[0])
            if reclaimed:
                log_event(log, "reclaimed orphaned tasks", count=reclaimed)
        except Exception as exc:  # noqa: BLE001 - a missing table before migration must not stop the worker
            log.warning("could not reclaim orphaned tasks: %s", exc.__class__.__name__)
        for n in range(self.concurrency):
            thread = threading.Thread(target=self._loop, name=f"worker-{n}", daemon=True)
            thread.start()
            self._threads.append(thread)
        log_event(log, "worker started", worker_id=q.WORKER_ID, concurrency=self.concurrency, queues=self.queues)

    def beat(self) -> None:
        """Touch the liveness file. The worker container's healthcheck reads it, so an
        idle worker still looks alive while a process whose threads died does not."""
        try:
            HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT.touch()
        except OSError:  # a read-only filesystem must not stop the worker
            pass

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        deadline = time.time() + timeout
        for thread in self._threads:
            thread.join(max(0.1, deadline - time.time()))
        log_event(log, "worker stopped", worker_id=q.WORKER_ID, processed=self.processed, failed=self.failed)

    def _semaphore(self, queue_name: str) -> threading.BoundedSemaphore | None:
        return self._ai_limit if queue_name in q.AI_QUEUES else None

    # --- main loop ----------------------------------------------------------------------
    def _loop(self) -> None:
        idle = 0
        while not self._stop.is_set():
            ran = False
            self.beat()
            try:
                ran = self.run_once()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                log.error("worker loop error: %s", exc.__class__.__name__, exc_info=True)
            if ran:
                idle = 0
            else:
                idle += 1
                self._stop.wait(min(self.poll_interval * (1 + idle * 0.25), 10.0))

    def run_once(self) -> bool:
        """Claim and run one task. Returns True when a task was processed."""
        with transaction(write=True) as session:
            task_row = q.claim(session, self.queues)
            if task_row is None:
                return False
            task_id = task_row.id
            task_type = task_row.task_type
            task_queue = task_row.queue
            correlation = task_row.correlation_id
        self._run_task(task_id, task_type, task_queue, correlation)
        return True

    def _run_task(self, task_id: int, task_type: str, task_queue: str, correlation: str) -> None:
        token = logctx.bind(task_id=task_id, task_type=task_type, correlation_id=correlation)
        semaphore = self._semaphore(task_queue)
        started = time.time()
        self.current[threading.current_thread().name] = f"{task_type}#{task_id}"
        bus.emit("task.started", entity_type="task", entity_id=task_id, task_type=task_type, queue=task_queue)
        try:
            if semaphore:
                semaphore.acquire()
            try:
                check_switches(task_queue)
                handler = _HANDLERS.get(task_type)
                if handler is None:
                    raise AppError(f"No handler registered for task type {task_type!r}", code="UNKNOWN_TASK", retryable=False)
                with transaction() as session:
                    task_row = session.get(AgentTask, task_id)
                    if task_row is None:
                        return
                    session.expunge(task_row)
                result = handler(task_row)
                with transaction(write=True) as session:
                    fresh = session.get(AgentTask, task_id)
                    if fresh is not None:
                        q.complete(session, fresh, result)
                self.processed += 1
                bus.emit("task.succeeded", entity_type="task", entity_id=task_id, task_type=task_type, queue=task_queue, duration_ms=int((time.time() - started) * 1000))
            finally:
                if semaphore:
                    semaphore.release()
        except AutomationPaused as exc:
            # Not a failure: put the task back for later without burning an attempt.
            with transaction(write=True) as session:
                fresh = session.get(AgentTask, task_id)
                if fresh is not None:
                    fresh.status = "queued"
                    fresh.attempts = max(0, fresh.attempts - 1)
                    fresh.locked_by = ""
                    fresh.run_at = datetime.now(UTC) + timedelta(seconds=30)
                    fresh.last_error = str(exc)
                    fresh.last_error_code = exc.code
            bus.emit("task.paused", entity_type="task", entity_id=task_id, task_type=task_type, reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded, classified, retried or dead-lettered
            self.failed += 1
            with transaction(write=True) as session:
                fresh = session.get(AgentTask, task_id)
                outcome = q.fail(session, fresh, exc) if fresh is not None else "missing"
            log_event(
                log,
                "task failed",
                level=40,
                outcome=outcome,
                error=f"{exc.__class__.__name__}: {exc}"[:500],
                trace=traceback.format_exc()[-1500:] if not isinstance(exc, AppError) else "",
            )
            bus.emit("task.failed", entity_type="task", entity_id=task_id, task_type=task_type, queue=task_queue, outcome=outcome, error=f"{exc.__class__.__name__}: {exc}"[:300])
            if outcome == "dead":
                bus.emit("worker.error", entity_type="task", entity_id=task_id, task_type=task_type, message="Task moved to dead-letter queue")
        finally:
            self.current.pop(threading.current_thread().name, None)
            logctx.unbind(token)

    # --- status for the Automation Center ----------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "worker_id": q.WORKER_ID,
            "started_at": self.started_at.isoformat(),
            "concurrency": self.concurrency,
            "processed": self.processed,
            "failed": self.failed,
            "current": dict(self.current),
            "queues": self.queues,
        }


async def run_forever(worker: Worker, stop_event: asyncio.Event | None = None) -> None:
    worker.start()
    try:
        await (stop_event or asyncio.Event()).wait()
    finally:
        worker.stop()
