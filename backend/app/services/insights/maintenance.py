"""Keeping the insight tables small and checking the public addresses.

Retention: request minutes 2 hours (folded into hours), hours 90 days (folded into days),
days for good; telemetry and uptime checks 30 days; resolved alerts 180 days.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import settings
from ...core.logging import get_logger
from ...models import (
    InsightAlert,
    RequestMetric,
    TelemetryEvent,
    UptimeCheck,
)
from .collector import BUCKETS

log = get_logger(__name__)


def fold(session: Session, source: str, target: str, older_than: datetime) -> int:
    """Sum `source` rows older than the cut into `target` buckets (hour or day), then delete them."""
    rows = session.scalars(select(RequestMetric).where(RequestMetric.granularity == source, RequestMetric.bucket < older_than).limit(50_000)).all()
    if not rows:
        return 0
    merged: dict[tuple[datetime, str, str, str], list[Any]] = defaultdict(lambda: [0, 0.0, 0.0, [0] * len(BUCKETS)])
    for r in rows:
        bucket = r.bucket.replace(minute=0, second=0, microsecond=0) if target == "hour" else r.bucket.replace(hour=0, minute=0, second=0, microsecond=0)
        item = merged[(bucket, r.app, r.route, r.status_class)]
        item[0] += r.count
        item[1] += r.sum_ms
        item[2] = max(item[2], r.max_ms)
        item[3] = [a + b for a, b in zip(item[3], r.histogram or [0] * len(BUCKETS), strict=False)]
        session.delete(r)
    for (bucket, app, route, cls), (count, total, peak, hist) in merged.items():
        session.add(RequestMetric(granularity=target, bucket=bucket, app=app, route=route, status_class=cls, count=count, sum_ms=round(total, 1), max_ms=peak, histogram=hist))
    session.flush()
    return len(rows)


def _deleted(session: Session, stmt: Any) -> int:
    return int(getattr(session.execute(stmt), "rowcount", 0) or 0)


def prune(session: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    out = {
        "minutes_folded": fold(session, "minute", "hour", now - timedelta(hours=2)),
        "hours_folded": fold(session, "hour", "day", now - timedelta(days=90)),
    }
    for model, column, days, name in ((TelemetryEvent, TelemetryEvent.created_at, 30, "telemetry"), (UptimeCheck, UptimeCheck.checked_at, 30, "uptime")):
        out[name] = _deleted(session, delete(model).where(column < now - timedelta(days=days)))
    out["alerts"] = _deleted(session, delete(InsightAlert).where(InsightAlert.resolved_at < now - timedelta(days=180)))
    return out


# --- uptime ------------------------------------------------------------------------------------------
def targets() -> list[tuple[str, str]]:
    cfg = settings()
    if cfg.insights_uptime_targets.strip():
        pairs = [part.split("=", 1) for part in cfg.csv(cfg.insights_uptime_targets) if "=" in part]
        return [(name.strip()[:40], url.strip()) for name, url in pairs]
    out: list[tuple[str, str]] = []
    admin = (cfg.admin_public_base_url or "").rstrip("/")
    if admin.startswith("https://"):
        out.append(("Admin", f"{admin}/admin-api/health"))
    site = (cfg.public_site_url or "").rstrip("/")
    if site.startswith("https://"):
        out.append(("Website", f"{site}/"))
    return out


def check_uptime(session: Session) -> int:
    now = datetime.now(UTC)
    done = 0
    with httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": "ControlTower-Uptime/1"}) as client:
        for name, url in targets():
            started = time.perf_counter()
            try:
                status = client.get(url).status_code
            except httpx.HTTPError:
                status = 0
            ms = (time.perf_counter() - started) * 1000
            session.add(UptimeCheck(checked_at=now, target=name, ok=200 <= status < 400, status=status, ms=round(ms, 1)))
            done += 1
    return done
