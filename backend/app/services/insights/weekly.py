"""The Monday report: last week's numbers against the week before, e-mailed to ADMIN_ALERT_EMAIL
at 07:00 in the owner's time zone (worker task, hourly check)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ...config import settings
from ...core import clock
from .collector import local_day
from .queries import BY_KEY, kpi

LINES = ("agent_runs", "proposals", "cleared", "ai_cost", "server_errors")


def fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "–"
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "hours":
        return f"{value:.1f} h"
    return f"{value:,.1f}".rstrip("0").rstrip(".") if value % 1 else f"{int(value):,}"


def change(value: float | None, previous: float | None) -> str:
    if value is None or previous is None:
        return ""
    if previous == 0:
        return " (new)" if value else ""
    pct = (value - previous) / previous * 100
    return f" ({'+' if pct >= 0 else ''}{pct:.0f} %)"


def report(session: Session, week_end: date) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in LINES:
        spec = BY_KEY[key]
        item = kpi(session, spec, 7, week_end)
        values[key] = fmt(item["value"], spec.unit) + change(item["value"], item["previous"])
    values["week"] = f"{(week_end - timedelta(days=6)).isoformat()} – {week_end.isoformat()}"
    return values


def due(session: Session, now: datetime) -> date | None:
    """The Sunday the report covers, if it is Monday after 07:00 and it has not gone out yet."""
    from .. import platform

    local = now.astimezone(clock.zone())
    if local.weekday() != 0 or local.hour < 7:
        return None
    week_end = local_day(now) - timedelta(days=1)
    stored = platform.get(session, "insights")
    if isinstance(stored, dict) and stored.get("weekly_sent") == week_end.isoformat():
        return None
    return week_end


def send(session: Session, week_end: date) -> bool:
    from .. import platform, system_mail

    to = settings().admin_alert_email.strip()
    if not to:
        return False
    base = (settings().admin_public_base_url or "").rstrip("/")
    system_mail.send("insights_weekly", to=to, name="", locale="en", link=f"{base}/insights", extra=report(session, week_end))
    stored: dict[str, Any] = dict(platform.get(session, "insights") or {})
    stored["weekly_sent"] = week_end.isoformat()
    platform.put(session, "insights", stored)
    return True
