"""Standalone worker + scheduler process: `python -m app.worker`.

Handles SIGTERM gracefully: stops claiming, lets running tasks finish (bounded),
then exits. Tasks left `running` by a hard kill are re-queued by the stale-lock
maintenance job.
"""

from __future__ import annotations

import signal
import sys
import threading
import time

from .heartbeat import HEARTBEAT

# The rest of the app is imported inside main(): the healthcheck (`--check`) runs
# every 30 s with a 5 s limit, and loading every agent takes ~4 s on its own.
# Longest a healthy worker can go without touching the heartbeat: the idle poll backs
# off to 10 s, plus room for a slow task to finish.
STALE_AFTER_SECONDS = 120


def check() -> int:
    """`python -m app.worker --check`: the worker container's healthcheck."""
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except OSError:
        print("worker heartbeat missing")
        return 1
    if age > STALE_AFTER_SECONDS:
        print(f"worker heartbeat is {int(age)}s old")
        return 1
    return 0


def main() -> None:
    from .core.logging import configure_logging, get_logger
    from .workers import handlers as _handlers  # noqa: F401 - registers task handlers
    from .workers.scheduler import Scheduler
    from .workers.worker import Worker

    log = get_logger(__name__)
    configure_logging()
    worker = Worker()
    scheduler = Scheduler()
    stop = threading.Event()

    def _term(*_: object) -> None:
        log.info("worker received stop signal")
        stop.set()

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    scheduler.start()
    worker.start()
    stop.wait()
    scheduler.stop()
    worker.stop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(check())
    main()
