"""Reading the insight tables for the dashboard and the weekly report.

Every number is a sum, an average or the latest value of `kpi_daily` rows over a period of
the owner's days, compared with the period of the same length just before it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import (
    ErrorGroup,
    InsightAlert,
    KpiDaily,
    RequestMetric,
    TelemetryEvent,
    UptimeCheck,
)
from .collector import BUCKETS, local_day
from .snapshot import hist_percentile, percentile

Agg = Literal["sum", "avg", "last", "max"]
Unit = Literal["count", "eur_cents", "usd", "ms", "hours", "percent"]


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    metric: str
    dimension: str = ""  # "*" = every dimension added up
    agg: Agg = "sum"
    unit: Unit = "count"
    good: Literal["up", "down", "neutral"] = "up"
    hint: str = ""


# The install's own numbers, then the plugins' (services/plugins adds and removes theirs: `<plugin>_<metric>`).
KPIS: list[Kpi] = [
    Kpi("agent_runs", "Agent runs", "agents.runs", "*", good="neutral"),
    Kpi("proposals", "Proposals made", "clearance.proposed", good="neutral", hint="What the agents asked clearance for."),
    Kpi("cleared", "Cleared", "clearance.decided", "approved", good="neutral", hint="Proposals the owner approved."),
    Kpi("rejected", "Rejected", "clearance.decided", "rejected", good="down"),
    Kpi("ai_cost", "AI cost", "ai.cost_usd", unit="usd", good="down"),
    Kpi("requests", "Requests", "requests.count", "*", good="neutral"),
    Kpi("server_errors", "Server errors", "requests.errors", "*", good="down"),
    Kpi("client_errors", "Errors in browsers", "errors", "*", good="down"),
]
BY_KEY = {k.key: k for k in KPIS}


def period(days: int, end: date | None = None) -> tuple[date, date]:
    last = end or local_day()
    return last - timedelta(days=days - 1), last


def daily(session: Session, metric: str, dimension: str, start: date, end: date) -> dict[date, float]:
    stmt = select(KpiDaily.day, func.sum(KpiDaily.value)).where(KpiDaily.metric == metric, KpiDaily.day >= start, KpiDaily.day <= end)
    if dimension != "*":
        stmt = stmt.where(KpiDaily.dimension == dimension)
    return {d: float(v or 0) for d, v in session.execute(stmt.group_by(KpiDaily.day)).all()}


def aggregate(values: dict[date, float], agg: Agg, start: date, end: date) -> float | None:
    if not values:
        return None if agg in ("last", "avg") else 0.0
    if agg == "sum":
        return sum(values.values())
    if agg == "max":
        return max(values.values())
    if agg == "last":
        return values[max(values)]
    span = (end - start).days + 1
    return sum(values.values()) / span


def kpi(session: Session, spec: Kpi, days: int, end: date | None = None) -> dict[str, Any]:
    start, last = period(days, end)
    prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)
    now_values = daily(session, spec.metric, spec.dimension, start, last)
    before = daily(session, spec.metric, spec.dimension, prev_start, prev_end)
    spark = [now_values.get(start + timedelta(days=i), 0.0) for i in range(days)]
    value, previous = aggregate(now_values, spec.agg, start, last), aggregate(before, spec.agg, prev_start, prev_end)
    return {"key": spec.key, "label": spec.label, "unit": spec.unit, "good": spec.good, "hint": spec.hint,
            "value": value, "previous": previous, "spark": spark, "has_data": bool(now_values or before)}


def series(session: Session, pairs: list[tuple[str, str]], days: int, end: date | None = None) -> dict[str, Any]:
    start, last = period(days, end)
    axis = [start + timedelta(days=i) for i in range(days)]
    out = []
    for metric, dimension in pairs:
        values = daily(session, metric, dimension, start, last)
        out.append({"metric": metric, "dimension": dimension, "values": [values.get(d, 0.0) for d in axis]})
    return {"days": [d.isoformat() for d in axis], "series": out}


def breakdown(session: Session, metric: str, days: int, *, agg: Agg = "sum", end: date | None = None, limit: int = 30) -> list[dict[str, Any]]:
    start, last = period(days, end)
    rows = session.execute(select(KpiDaily.dimension, KpiDaily.day, KpiDaily.value).where(KpiDaily.metric == metric, KpiDaily.day >= start, KpiDaily.day <= last)).all()
    per: dict[str, dict[date, float]] = defaultdict(dict)
    for dim, day, value in rows:
        per[dim][day] = per[dim].get(day, 0.0) + float(value)
    items: list[dict[str, Any]] = [{"dimension": dim, "value": aggregate(values, agg, start, last) or 0.0} for dim, values in per.items() if dim]
    items.sort(key=lambda x: -float(x["value"]))
    return items[:limit]


def latest(session: Session, metric: str) -> list[dict[str, Any]]:
    day = session.scalar(select(func.max(KpiDaily.day)).where(KpiDaily.metric == metric))
    if day is None:
        return []
    rows = session.execute(select(KpiDaily.dimension, KpiDaily.value).where(KpiDaily.metric == metric, KpiDaily.day == day)).all()
    return sorted(({"dimension": d, "value": float(v)} for d, v in rows), key=lambda x: -x["value"])


# --- performance (live tables, not the snapshot) ------------------------------------------------------
def performance(session: Session, hours: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    gran = ("minute", "hour") if hours <= 72 else ("hour", "day")
    rows = session.execute(select(RequestMetric.bucket, RequestMetric.app, RequestMetric.route, RequestMetric.status_class, RequestMetric.count,
                                  RequestMetric.sum_ms, RequestMetric.histogram).where(RequestMetric.bucket >= since, RequestMetric.granularity.in_(gran))).all()
    step = timedelta(hours=1) if hours <= 72 else timedelta(days=1)
    n = int(timedelta(hours=hours) / step)
    first = (now - step * (n - 1)).replace(minute=0, second=0, microsecond=0)
    if step == timedelta(days=1):
        first = first.replace(hour=0)
    axis = [first + step * i for i in range(n)]
    empty = [0] * len(BUCKETS)
    timeline: dict[str, dict[datetime, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "errors": 0, "hist": list(empty)}))
    routes: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"count": 0, "errors": 0, "sum": 0.0, "hist": list(empty)})
    for bucket, app, route, cls, count, total, hist in rows:
        b = bucket.replace(minute=0, second=0, microsecond=0) if step == timedelta(hours=1) else bucket.replace(hour=0, minute=0, second=0, microsecond=0)
        cell = timeline[app][b]
        cell["count"] += count
        cell["errors"] += count if cls == "5xx" else 0
        cell["hist"] = [x + y for x, y in zip(cell["hist"], hist or empty, strict=False)]
        r = routes[(app, route)]
        r["count"] += count
        r["errors"] += count if cls == "5xx" else 0
        r["sum"] += total
        r["hist"] = [x + y for x, y in zip(r["hist"], hist or empty, strict=False)]
    apps = []
    for app, cells in sorted(timeline.items()):
        total_hist = list(empty)
        for c in cells.values():
            total_hist = [x + y for x, y in zip(total_hist, c["hist"], strict=False)]
        count = sum(c["count"] for c in cells.values())
        errors = sum(c["errors"] for c in cells.values())
        apps.append({"app": app, "count": count, "errors": errors, "error_rate": (errors / count) if count else 0.0,
                     "p50": hist_percentile(total_hist, 0.5), "p95": hist_percentile(total_hist, 0.95),
                     "timeline": [{"at": t.isoformat(), "count": cells[t]["count"] if t in cells else 0, "errors": cells[t]["errors"] if t in cells else 0,
                                   "p95": hist_percentile(cells[t]["hist"], 0.95) if t in cells else None} for t in axis]})
    slow: list[dict[str, Any]] = [{"app": app, "route": route, "count": r["count"], "errors": r["errors"], "avg": r["sum"] / r["count"] if r["count"] else 0.0,
             "p95": hist_percentile(r["hist"], 0.95)} for (app, route), r in routes.items() if r["count"] >= 5]
    slow.sort(key=lambda x: (-float(x["p95"]), -int(x["count"])))
    return {"hours": hours, "axis": [t.isoformat() for t in axis], "apps": apps, "slowest": slow[:15],
            "busiest": sorted(slow, key=lambda x: -int(x["count"]))[:10], "uptime": uptime(session, since)}


def uptime(session: Session, since: datetime) -> list[dict[str, Any]]:
    out = []
    for (target,) in session.execute(select(UptimeCheck.target).where(UptimeCheck.checked_at >= since).distinct()).all():
        checks = session.execute(select(UptimeCheck.checked_at, UptimeCheck.ok, UptimeCheck.ms, UptimeCheck.status).where(UptimeCheck.target == target, UptimeCheck.checked_at >= since)
                                 .order_by(UptimeCheck.checked_at)).all()
        ok = sum(1 for c in checks if c.ok)
        recent = checks[-60:]
        out.append({"target": target, "checks": len(checks), "ok_share": ok / len(checks) if checks else None, "last_ok": checks[-1].ok if checks else None,
                    "last_status": checks[-1].status if checks else None, "avg_ms": sum(c.ms for c in checks) / len(checks) if checks else None,
                    "recent": [{"at": c.checked_at.isoformat(), "ok": c.ok, "ms": c.ms} for c in recent]})
    return sorted(out, key=lambda x: x["target"])


# Thresholds Google uses to call a page fast, needing work, or slow.
VITAL_LIMITS = {"LCP": (2500, 4000), "INP": (200, 500), "CLS": (0.1, 0.25), "TTFB": (800, 1800), "FCP": (1800, 3000)}


def vitals(session: Session, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(select(TelemetryEvent.app, TelemetryEvent.name, TelemetryEvent.page, TelemetryEvent.device, TelemetryEvent.value)
                           .where(TelemetryEvent.kind.in_(("vital", "timing")), TelemetryEvent.created_at >= since)).all()
    per: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_page: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    per_device: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for app, name, page, device, value in rows:
        per[(app, name)].append(value)
        per_page[(app, name, page)].append(value)
        per_device[(app, name, device)].append(value)

    def rating(name: str, value: float) -> str:
        limits = VITAL_LIMITS.get(name)
        if limits is None:
            return "none"
        good, poor = limits
        return "good" if value <= good else "poor" if value > poor else "needs_work"

    summary = [{"app": app, "name": name, "p75": percentile(v, 0.75), "samples": len(v), "rating": rating(name, percentile(v, 0.75)),
                "good_share": (sum(1 for x in v if rating(name, x) == "good") / len(v)) if v and name in VITAL_LIMITS else None} for (app, name), v in per.items()]
    pages: list[dict[str, Any]] = [{"app": app, "name": name, "page": page, "p75": percentile(v, 0.75), "samples": len(v), "rating": rating(name, percentile(v, 0.75))}
             for (app, name, page), v in per_page.items() if len(v) >= 3]
    pages.sort(key=lambda x: (x["rating"] != "poor", -float(x["p75"])))
    devices = [{"app": app, "name": name, "device": device, "p75": percentile(v, 0.75), "samples": len(v)} for (app, name, device), v in per_device.items()]
    return {"days": days, "summary": sorted(summary, key=lambda x: (x["app"], x["name"])), "pages": pages[:25], "devices": devices, "limits": VITAL_LIMITS}


def errors(session: Session, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
    from .telemetry import summary

    stmt = select(ErrorGroup)
    if status != "all":
        stmt = stmt.where(ErrorGroup.status == status)
    rows = session.scalars(stmt.order_by(ErrorGroup.last_seen.desc()).limit(limit)).all()
    out = [summary(r) for r in rows]
    day_ago = datetime.now(UTC) - timedelta(days=1)
    recent: dict[str, int] = {str(fp): int(n) for fp, n in session.execute(select(TelemetryEvent.fingerprint, func.count()).where(
        TelemetryEvent.fingerprint.in_([r.fingerprint for r in rows] or [""]), TelemetryEvent.created_at >= day_ago).group_by(TelemetryEvent.fingerprint)).all()}
    for item in out:
        item["last_24h"] = int(recent.get(item["fingerprint"], 0))
    return out


def alerts(session: Session, days: int = 30) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.scalars(select(InsightAlert).where((InsightAlert.resolved_at.is_(None)) | (InsightAlert.opened_at >= since)).order_by(InsightAlert.opened_at.desc()).limit(100)).all()
    return [{"id": a.id, "rule": a.rule, "subject": a.subject, "severity": a.severity, "detail": a.detail, "opened_at": a.opened_at, "last_seen_at": a.last_seen_at,
             "resolved_at": a.resolved_at} for a in rows]
