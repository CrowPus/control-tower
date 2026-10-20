"""Counting requests without slowing anyone down.

Each server process keeps counters in memory: per minute, app, route template and status
class a count, a sum, a maximum and a latency histogram. A background thread writes them
every minute (`flush`), in one short transaction. If writing fails, the counts of that
minute are dropped, never the request.

Route templates only (`/admin-api/team/{key}`), never real paths, ids or query strings.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ...config import settings
from ...core import clock
from ...core.logging import get_logger

log = get_logger(__name__)

# Upper bounds of the latency buckets in ms; the last one catches everything slower.
BUCKETS: tuple[float, ...] = (25, 50, 100, 200, 400, 800, 1600, 3200, 6400, float("inf"))

Db = Callable[[], AbstractContextManager[Session]]


def local_day(moment: datetime | None = None) -> date:
    return clock.local_day(moment or datetime.now(UTC))


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def app_of(path: str) -> str | None:
    """Which part of the install served the request (None: not counted, e.g. static files and health checks)."""
    if path.endswith("/health") or path.startswith("/admin-api/telemetry"):
        return None
    if path.startswith("/admin-api/"):
        return "admin"
    if path.startswith("/agent-gateway"):
        return "gateway"
    return None


def status_class(status: int) -> str:
    return f"{min(max(status // 100, 1), 5)}xx"


class Collector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[datetime, str, str, str], list[Any]] = {}
        self._thread: threading.Thread | None = None

    # --- recording (called on every request; must stay cheap) ---------------------------------
    def request(self, app: str, route: str, status: int, ms: float) -> None:
        minute = datetime.now(UTC).replace(second=0, microsecond=0)
        key = (minute, app, (route or "unmatched")[:120], status_class(status))
        index = next(i for i, bound in enumerate(BUCKETS) if ms <= bound)
        with self._lock:
            row = self._requests.get(key)
            if row is None:
                row = [0, 0.0, 0.0, [0] * len(BUCKETS)]
                self._requests[key] = row
            row[0] += 1
            row[1] += ms
            row[2] = max(row[2], ms)
            row[3][index] += 1

    # --- writing ----------------------------------------------------------------------------
    def take(self) -> dict[tuple[datetime, str, str, str], list[Any]]:
        with self._lock:
            requests, self._requests = self._requests, {}
        return requests

    def flush(self, db: Db) -> int:
        """Write what was counted. Returns the number of rows written."""
        from ...models import RequestMetric

        requests = self.take()
        if not requests:
            return 0
        try:
            with db() as session:
                for (minute, app, route, cls), (count, total, peak, hist) in requests.items():
                    session.add(RequestMetric(granularity="minute", bucket=minute, app=app, route=route, status_class=cls, count=count,
                                              sum_ms=round(total, 1), max_ms=round(peak, 1), histogram=hist))
        except Exception as exc:  # noqa: BLE001 - counting must never break the service
            log.warning("insights flush failed: %s", exc.__class__.__name__)
            return 0
        return len(requests)

    def start(self, db: Db, every: float = 60.0) -> None:
        """A daemon thread that flushes every minute (not in tests: they flush by hand)."""
        if self._thread is not None or settings().execution_mode == "test":
            return

        def loop() -> None:
            while True:
                time.sleep(every)
                self.flush(db)

        self._thread = threading.Thread(target=loop, name="insights-flush", daemon=True)
        self._thread.start()


COLLECTOR = Collector()


def route_template(scope: Any) -> str:
    route = scope.get("route")
    path = getattr(route, "path", "") or ""
    return path or "unmatched"
