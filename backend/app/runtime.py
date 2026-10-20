"""Process-level runtime state: the embedded worker/scheduler handles."""

from __future__ import annotations

from typing import Any

from .workers.scheduler import Scheduler
from .workers.worker import Worker

_worker: Worker | None = None
_scheduler: Scheduler | None = None


def start_embedded(*, worker: bool, scheduler: bool) -> None:
    global _worker, _scheduler
    from .workers import handlers as _handlers  # noqa: F401 - registers task handlers

    if worker and _worker is None:
        _worker = Worker()
        _worker.start()
    if scheduler and _scheduler is None:
        _scheduler = Scheduler()
        _scheduler.start()


def stop_embedded() -> None:
    global _worker, _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
    if _worker is not None:
        _worker.stop(timeout=15)
        _worker = None


def worker_status() -> dict[str, Any]:
    if _worker is None:
        return {"embedded": False, "processed": 0, "failed": 0, "current": {}}
    status = _worker.status()
    status["embedded"] = True
    status["scheduler_last_tick_at"] = _scheduler.last_tick_at.isoformat() if _scheduler and _scheduler.last_tick_at else None
    return status


def worker() -> Worker | None:
    return _worker
