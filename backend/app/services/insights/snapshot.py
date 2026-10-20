"""The daily numbers: computed by the worker from the live tables, written to `kpi_daily`, so
the history survives purges and the admin reads one small table.

`run(session, day)` replaces that day's rows. Today is recomputed every 15 minutes (so the
dashboard is current), yesterday once more after midnight (so it is final). `backfill`
rebuilds every day since the install's first record from what still exists.

Days are the owner's calendar days (TIME_ZONE). Counts only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...core import clock
from ...models import (
    AgentTask,
    AIUsage,
    EmailDelivery,
    ErrorGroup,
    KpiDaily,
    RequestMetric,
    TeamApproval,
    TeamRun,
    TelemetryEvent,
    UptimeCheck,
)
from .collector import BUCKETS, local_day

Row = tuple[str, str, float]
# Metrics written by other tasks than this one (a plugin's own numbers, from CT3): a day's rerun leaves them alone.
FOREIGN_PREFIXES = ("plugin.",)


def bounds(day: date) -> tuple[datetime, datetime]:
    zone = clock.zone()
    start = datetime.combine(day, time(0, 0), zone).astimezone(UTC)
    return start, datetime.combine(day + timedelta(days=1), time(0, 0), zone).astimezone(UTC)


def _count(session: Session, column: Any, start: datetime, end: datetime, *where: Any) -> int:
    stmt = select(func.count()).where(column >= start, column < end, *where)
    return int(session.scalar(stmt) or 0)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return float(ordered[index])


def hist_percentile(hist: list[int], q: float) -> float:
    """A percentile from bucket counts, interpolated linearly inside the bucket it falls in
    (the open last bucket counts as reaching twice the last bound)."""
    total = sum(hist)
    if not total:
        return 0.0
    target, seen = q * total, 0
    for i, n in enumerate(hist):
        if n and seen + n >= target:
            low = BUCKETS[i - 1] if i else 0.0
            high = BUCKETS[i] if BUCKETS[i] != float("inf") else BUCKETS[-2] * 2
            return round(float(low + (high - low) * (target - seen) / n), 1)
        seen += n
    return float(BUCKETS[-2])


# --- the groups of numbers -----------------------------------------------------------------------
def agents(session: Session, day: date) -> list[Row]:
    """The agents' work: runs per agent and outcome, proposals made, and the owner's clearance decisions."""
    s, e = bounds(day)
    rows: list[Row] = []
    for agent, status, n, cost in session.execute(select(TeamRun.agent, TeamRun.status, func.count(), func.coalesce(func.sum(TeamRun.cost_usd), 0.0))
                                                  .where(TeamRun.started_at >= s, TeamRun.started_at < e).group_by(TeamRun.agent, TeamRun.status)).all():
        rows += [("agents.runs", f"{agent}:{status}", float(n)), ("agents.cost_usd", str(agent), float(cost or 0))]
    rows.append(("clearance.proposed", "", float(_count(session, TeamApproval.created_at, s, e))))
    for status, n in session.execute(select(TeamApproval.status, func.count()).where(TeamApproval.decided_at >= s, TeamApproval.decided_at < e)
                                     .group_by(TeamApproval.status)).all():
        rows.append(("clearance.decided", str(status), float(n)))
    return rows


def ai(session: Session, day: date) -> list[Row]:
    s, e = bounds(day)
    rows: list[Row] = []
    data = session.execute(select(AIUsage.operation, AIUsage.estimated_cost_usd, AIUsage.latency_ms, AIUsage.success).where(AIUsage.created_at >= s, AIUsage.created_at < e)).all()
    by_op: dict[str, list[Any]] = defaultdict(list)
    for op, cost, latency, ok in data:
        by_op[(op or "other").split(".")[0]].append((float(cost or 0), float(latency or 0), bool(ok)))
    total = 0.0
    for op, items in by_op.items():
        cost = sum(c for c, _l, _ok in items)
        total += cost
        rows += [("ai.cost_usd", op, round(cost, 4)), ("ai.calls", op, float(len(items))), ("ai.failures", op, float(sum(1 for *_x, ok in items if not ok))),
                 ("ai.latency_p50_ms", op, percentile([lat for _c, lat, _ok in items], 0.5))]
    rows.append(("ai.cost_usd", "", round(total, 4)))
    return rows


def platform(session: Session, day: date) -> list[Row]:
    s, e = bounds(day)
    rows: list[Row] = []
    for status, n in session.execute(select(AgentTask.status, func.count()).where(AgentTask.updated_at >= s, AgentTask.updated_at < e).group_by(AgentTask.status)).all():
        rows.append(("tasks", str(status), float(n)))
    for status, n in session.execute(select(EmailDelivery.status, func.count()).where(EmailDelivery.created_at >= s, EmailDelivery.created_at < e).group_by(EmailDelivery.status)).all():
        rows.append(("email", str(status), float(n)))
    return rows


def requests(session: Session, day: date) -> list[Row]:
    s, e = bounds(day)
    stmt = select(RequestMetric.app, RequestMetric.status_class, RequestMetric.count, RequestMetric.sum_ms, RequestMetric.histogram).where(
        RequestMetric.bucket >= s, RequestMetric.bucket < e, RequestMetric.granularity.in_(("minute", "hour")))
    per_app: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "errors": 0, "client_errors": 0, "sum": 0.0, "hist": [0] * len(BUCKETS)})
    for app, cls, count, total, hist in session.execute(stmt).all():
        item = per_app[app]
        item["count"] += count
        item["sum"] += total
        item["errors"] += count if cls == "5xx" else 0
        item["client_errors"] += count if cls == "4xx" else 0
        item["hist"] = [a + b for a, b in zip(item["hist"], hist or [0] * len(BUCKETS), strict=False)]
    rows: list[Row] = []
    for app, item in per_app.items():
        rows += [("requests.count", app, float(item["count"])), ("requests.errors", app, float(item["errors"])), ("requests.client_errors", app, float(item["client_errors"])),
                 ("requests.p50_ms", app, hist_percentile(item["hist"], 0.5)), ("requests.p95_ms", app, hist_percentile(item["hist"], 0.95))]
    return rows


def errors_and_uptime(session: Session, day: date) -> list[Row]:
    s, e = bounds(day)
    rows: list[Row] = []
    vitals: dict[tuple[str, str], list[float]] = defaultdict(list)
    for app, name, value in session.execute(select(TelemetryEvent.app, TelemetryEvent.name, TelemetryEvent.value).where(
            TelemetryEvent.created_at >= s, TelemetryEvent.created_at < e, TelemetryEvent.kind.in_(("vital", "timing")))).all():
        vitals[(app, name)].append(float(value))
    rows += [("vitals.p75", f"{app}:{name}", round(percentile(v, 0.75), 3)) for (app, name), v in vitals.items()]
    for app, kind, n in session.execute(select(TelemetryEvent.app, TelemetryEvent.kind, func.count()).where(TelemetryEvent.created_at >= s, TelemetryEvent.created_at < e,
                                        TelemetryEvent.kind.in_(("error", "crash"))).group_by(TelemetryEvent.app, TelemetryEvent.kind)).all():
        rows.append(("errors" if kind == "error" else "crashes", app, float(n)))
    rows.append(("errors.new_groups", "", float(_count(session, ErrorGroup.first_seen, s, e))))
    for target, ok, n in session.execute(select(UptimeCheck.target, UptimeCheck.ok, func.count()).where(UptimeCheck.checked_at >= s, UptimeCheck.checked_at < e)
                                         .group_by(UptimeCheck.target, UptimeCheck.ok)).all():
        rows.append(("uptime.checks_ok" if ok else "uptime.checks_failed", target, float(n)))
    return rows


GROUPS = (agents, ai, platform, requests, errors_and_uptime)


def compute(session: Session, day: date) -> list[Row]:
    rows: list[Row] = []
    for group in GROUPS:
        rows += group(session, day)
    return rows


def run(session: Session, day: date) -> int:
    """Replace one day's numbers (except those other tasks write)."""
    rows = compute(session, day)
    merged: dict[tuple[str, str], float] = {}
    for metric, dimension, value in rows:
        merged[(metric, dimension[:80])] = merged.get((metric, dimension[:80]), 0.0) + float(value)
    stmt = delete(KpiDaily).where(KpiDaily.day == day)
    for prefix in FOREIGN_PREFIXES:
        stmt = stmt.where(~KpiDaily.metric.startswith(prefix))
    session.execute(stmt)
    for (metric, dimension), value in merged.items():
        session.add(KpiDaily(day=day, metric=metric, dimension=dimension, value=value))
    session.flush()
    return len(merged)


def backfill(session: Session, until: date | None = None) -> int:
    """Every day since the first AI call or agent run, oldest first."""
    firsts = [session.scalar(select(func.min(AIUsage.created_at))), session.scalar(select(func.min(TeamRun.started_at)))]
    known = [f for f in firsts if f is not None]
    if not known:
        return 0
    day, last, done = local_day(min(known)), until or local_day(), 0
    while day <= last:
        run(session, day)
        day += timedelta(days=1)
        done += 1
    return done


def days_with_data(session: Session) -> int:
    return int(session.scalar(select(func.count(func.distinct(KpiDaily.day)))) or 0)
