"""Alerts: rules checked every 5 minutes by the worker. An incident opens once (admin bell +
e-mail to ADMIN_ALERT_EMAIL), stays open while the rule still fires, and closes with a
"resolved" e-mail when it clears. Thresholds are editable in Insights → Settings (platform
setting `insights.alerts`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import (
    AgentTask,
    AIUsage,
    EmailDelivery,
    ErrorGroup,
    InsightAlert,
    RequestMetric,
    TelemetryEvent,
    UptimeCheck,
)
from .collector import BUCKETS
from .snapshot import hist_percentile

DEFAULTS: dict[str, float] = {
    "uptime_failures": 2,          # consecutive failed checks of one address
    "p95_ms": 2000,                # over the last 10 minutes
    "error_rate": 0.02,            # server errors (5xx) share, last 10 minutes
    "min_requests": 50,            # below this, speed and error rules stay quiet
    "new_error_events": 5,         # a new error type seen this often within an hour
    "crashes_per_hour": 5,
    "queue_stuck_minutes": 15,
    "bounce_rate": 0.05,           # e-mails, last 24 hours
    "min_emails": 20,
    "ai_budget_share": 0.8,        # of the daily AI cap
}


# Readable names for alert subjects (they go out by e-mail).
APP_NAMES = {"admin": "The admin", "gateway": "The agent gateway"}


def _name(app: str) -> str:
    return APP_NAMES.get(app, app)


@dataclass
class Finding:
    rule: str
    subject: str
    severity: str = "warning"
    detail: dict[str, Any] = field(default_factory=dict)


def thresholds(session: Session) -> dict[str, float]:
    from .. import platform

    stored = platform.get(session, "insights")
    raw = stored.get("alerts", {}) if isinstance(stored, dict) else {}
    out = dict(DEFAULTS)
    for key, value in (raw or {}).items():
        if key in DEFAULTS:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def evaluate(session: Session, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(UTC)
    t = thresholds(session)
    found: list[Finding] = []

    # Public addresses down.
    for (target,) in session.execute(select(UptimeCheck.target).where(UptimeCheck.checked_at >= now - timedelta(minutes=30)).distinct()).all():
        last = session.scalars(select(UptimeCheck.ok).where(UptimeCheck.target == target).order_by(UptimeCheck.checked_at.desc()).limit(int(t["uptime_failures"]))).all()
        if len(last) >= int(t["uptime_failures"]) and not any(last):
            found.append(Finding(f"uptime:{target}", f"{target} is not reachable", "critical", {"failed_checks": len(last)}))

    # Speed and server errors per app, last 10 minutes.
    per_app: dict[str, dict[str, Any]] = {}
    for app, cls, count, hist in session.execute(select(RequestMetric.app, RequestMetric.status_class, RequestMetric.count, RequestMetric.histogram)
                                                 .where(RequestMetric.granularity == "minute", RequestMetric.bucket >= now - timedelta(minutes=10))).all():
        item = per_app.setdefault(app, {"count": 0, "errors": 0, "hist": [0] * len(BUCKETS)})
        item["count"] += count
        item["errors"] += count if cls == "5xx" else 0
        item["hist"] = [a + b for a, b in zip(item["hist"], hist or [0] * len(BUCKETS), strict=False)]
    for app, item in per_app.items():
        if item["count"] < t["min_requests"]:
            continue
        p95 = hist_percentile(item["hist"], 0.95)
        if p95 > t["p95_ms"]:
            found.append(Finding(f"slow:{app}", f"{_name(app)} is slow: p95 {int(p95)} ms", "warning", {"p95_ms": p95, "requests": item["count"]}))
        rate = item["errors"] / item["count"]
        if rate > t["error_rate"]:
            found.append(Finding(f"errors:{app}", f"{_name(app)}: {rate:.1%} server errors", "critical", {"error_rate": rate, "requests": item["count"]}))

    # New error types and crashes in the last hour.
    for group in session.scalars(select(ErrorGroup).where(ErrorGroup.first_seen >= now - timedelta(hours=1), ErrorGroup.count >= t["new_error_events"], ErrorGroup.status == "open")):
        found.append(Finding(f"error:{group.fingerprint}", f"New {group.kind} in {_name(group.app).removeprefix('The ').lower()}: {group.error_type}", "warning", {"count": group.count, "page": group.page}))
    crashes = session.scalar(select(func.count()).select_from(TelemetryEvent).where(TelemetryEvent.kind == "crash", TelemetryEvent.created_at >= now - timedelta(hours=1))) or 0
    if crashes >= t["crashes_per_hour"]:
        found.append(Finding("crashes", f"{crashes} app crashes in the last hour", "critical", {"crashes": crashes}))

    # Background work.
    oldest = session.scalar(select(func.min(AgentTask.run_at)).where(AgentTask.status == "queued", AgentTask.run_at <= now))
    if oldest is not None and oldest < now - timedelta(minutes=t["queue_stuck_minutes"]):
        found.append(Finding("queue_stuck", "Background tasks are waiting too long", "critical", {"oldest_waiting_minutes": int((now - oldest).total_seconds() // 60)}))
    dead = session.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status == "dead", AgentTask.updated_at >= now - timedelta(hours=1))) or 0
    if dead:
        found.append(Finding("dead_tasks", f"{dead} background tasks gave up in the last hour", "warning", {"dead": dead}))

    # E-mail delivery.
    mails: dict[str, int] = {str(s): int(n) for s, n in session.execute(select(EmailDelivery.status, func.count()).where(EmailDelivery.created_at >= now - timedelta(hours=24))
                                                                          .group_by(EmailDelivery.status)).all()}
    sent = sum(mails.values())
    if sent >= t["min_emails"] and (mails.get("bounced", 0) / sent) > t["bounce_rate"]:
        found.append(Finding("email_bounces", f"{mails.get('bounced', 0)} of {sent} e-mails bounced today", "warning", {"bounced": mails.get("bounced", 0), "sent": sent}))

    # AI budget.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spend = float(session.scalar(select(func.coalesce(func.sum(AIUsage.estimated_cost_usd), 0.0)).where(AIUsage.created_at >= day_start)) or 0)
    cap = settings().ai_daily_budget_usd
    if cap and spend >= cap * t["ai_budget_share"]:
        found.append(Finding("ai_budget", f"AI spend today ${spend:.2f} of ${cap:.2f}", "warning", {"spend": spend, "cap": cap}))
    return found


def sync(session: Session, findings: list[Finding], now: datetime | None = None) -> dict[str, int]:
    """Open new incidents, keep firing ones, resolve the ones that cleared. E-mails once each way."""
    from ..bells import alert_admins

    now = now or datetime.now(UTC)
    open_rows = {a.rule: a for a in session.scalars(select(InsightAlert).where(InsightAlert.resolved_at.is_(None)))}
    firing = {f.rule: f for f in findings}
    opened = resolved = 0
    for rule, finding in firing.items():
        row = open_rows.get(rule)
        if row is not None:
            row.last_seen_at = now
            row.detail = finding.detail
            continue
        session.add(InsightAlert(rule=rule, subject=finding.subject[:120], severity=finding.severity, detail=finding.detail, opened_at=now, last_seen_at=now))
        opened += 1
        alert_admins(("URGENT: " if finding.severity == "critical" else "") + finding.subject, "/insights/alerts")
        # Agents that subscribe to alert.opened wake for it.
        from ..team import events

        events.emit(session, "alert.opened", "insights", {"rule": rule, "subject": finding.subject[:120], "severity": finding.severity, "detail": finding.detail})
    for rule, row in open_rows.items():
        if rule not in firing:
            row.resolved_at = now
            resolved += 1
            alert_admins(f"Resolved: {row.subject}", "/insights/alerts")
    session.flush()
    return {"opened": opened, "resolved": resolved, "open": len(firing)}
