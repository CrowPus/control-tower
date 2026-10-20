"""Insights in the admin: how the install and its agents are doing.

Read-only for both admin roles; closing an error group or an alert and changing thresholds
is for owners. Everything comes from the insight tables (counts and timings, no person).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import NotFound, ValidationFailed
from ..models import ErrorGroup, InsightAlert
from ..services import platform
from ..services.insights import alerts as alert_rules
from ..services.insights import queries
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api/insights", tags=["admin-insights"])

Days = Query(default=30, ge=1, le=730)


class InsightKpi(BaseModel):
    key: str
    label: str
    unit: str
    good: str
    hint: str = ""
    value: float | None
    previous: float | None
    spark: list[float]
    has_data: bool


class InsightSeries(BaseModel):
    metric: str
    dimension: str
    values: list[float]


class InsightSeriesOut(BaseModel):
    days: list[str]
    series: list[InsightSeries]


class InsightBreakdownItem(BaseModel):
    dimension: str
    value: float


class InsightAlertOut(BaseModel):
    id: int
    rule: str
    subject: str
    severity: str
    detail: dict[str, Any]
    opened_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class InsightTodayOut(BaseModel):
    days: int
    north_star: InsightKpi
    kpis: list[InsightKpi]
    activity: InsightSeriesOut
    alerts: list[InsightAlertOut]
    data_since: str | None
    updated_at: str | None


class InsightGroupOut(BaseModel):
    days: int
    kpis: list[InsightKpi]
    series: InsightSeriesOut
    breakdowns: dict[str, list[InsightBreakdownItem]]


class InsightPerformanceOut(BaseModel):
    hours: int
    axis: list[str]
    apps: list[dict[str, Any]]
    slowest: list[dict[str, Any]]
    busiest: list[dict[str, Any]]
    uptime: list[dict[str, Any]]
    vitals: dict[str, Any]
    errors: list[dict[str, Any]]


class InsightErrorStatusIn(BaseModel):
    status: Literal["open", "resolved", "ignored"]


class InsightSettings(BaseModel):
    alerts: dict[str, float] = Field(default_factory=dict)


def _kpis(session: Any, keys: tuple[str, ...], days: int) -> list[InsightKpi]:
    return [InsightKpi(**queries.kpi(session, queries.BY_KEY[k], days)) for k in keys]


def _series(session: Any, pairs: list[tuple[str, str]], days: int) -> InsightSeriesOut:
    data = queries.series(session, pairs, days)
    return InsightSeriesOut(days=data["days"], series=[InsightSeries(**s) for s in data["series"]])


def _breakdown(session: Any, metric: str, days: int, **kw: Any) -> list[InsightBreakdownItem]:
    return [InsightBreakdownItem(**x) for x in queries.breakdown(session, metric, days, **kw)]


@router.get("/today", response_model=InsightTodayOut)
def today(ctx: CurrentAdmin, session: AdminDb, days: int = Days) -> InsightTodayOut:
    from sqlalchemy import func

    from ..models import KpiDaily

    first = session.scalar(select(func.min(KpiDaily.day)))
    return InsightTodayOut(
        days=days,
        north_star=_kpis(session, ("cleared",), days)[0],
        kpis=_kpis(session, ("agent_runs", "proposals", "rejected", "ai_cost", "server_errors"), days),
        activity=_series(session, [("agents.runs", "*"), ("clearance.proposed", "")], days),
        alerts=[InsightAlertOut(**a) for a in queries.alerts(session, 7) if a["resolved_at"] is None],
        data_since=first.isoformat() if first else None,
        updated_at=str(session.scalar(select(func.max(KpiDaily.day)))) if first else None,
    )


@router.get("/agents", response_model=InsightGroupOut)
def agents(ctx: CurrentAdmin, session: AdminDb, days: int = Days) -> InsightGroupOut:
    """The agents' work: runs per agent and outcome, their AI cost, and the owner's clearance decisions."""
    return InsightGroupOut(
        days=days,
        kpis=_kpis(session, ("agent_runs", "proposals", "cleared", "rejected"), days),
        series=_series(session, [("agents.runs", "*"), ("clearance.proposed", ""), ("clearance.decided", "approved"), ("clearance.decided", "rejected")], days),
        breakdowns={"agents.runs": _breakdown(session, "agents.runs", days), "agents.cost_usd": _breakdown(session, "agents.cost_usd", days),
                    "clearance.decided": _breakdown(session, "clearance.decided", days)},
    )


@router.get("/money", response_model=InsightGroupOut)
def money(ctx: CurrentAdmin, session: AdminDb, days: int = Days) -> InsightGroupOut:
    return InsightGroupOut(
        days=days,
        kpis=_kpis(session, ("ai_cost",), days),
        series=_series(session, [("ai.cost_usd", "")], days),
        breakdowns={"ai.cost_usd": _breakdown(session, "ai.cost_usd", days),
                    "ai.calls": _breakdown(session, "ai.calls", days), "ai.failures": _breakdown(session, "ai.failures", days),
                    "ai.latency_p50_ms": _breakdown(session, "ai.latency_p50_ms", days, agg="avg")},
    )


@router.get("/platform", response_model=InsightGroupOut)
def platform_health(ctx: CurrentAdmin, session: AdminDb, days: int = Days) -> InsightGroupOut:
    return InsightGroupOut(
        days=days,
        kpis=_kpis(session, ("requests", "server_errors", "client_errors"), days),
        series=_series(session, [("requests.count", "*"), ("requests.errors", "*"), ("errors", "*")], days),
        breakdowns={"tasks": _breakdown(session, "tasks", days), "email": _breakdown(session, "email", days),
                    "requests.count": _breakdown(session, "requests.count", days), "requests.p95_ms": _breakdown(session, "requests.p95_ms", days, agg="avg")},
    )


@router.get("/performance", response_model=InsightPerformanceOut)
def performance(ctx: CurrentAdmin, session: AdminDb, hours: int = Query(default=24, ge=1, le=24 * 90)) -> InsightPerformanceOut:
    data = queries.performance(session, hours)
    days = max(1, min(30, hours // 24 or 1))
    return InsightPerformanceOut(**data, vitals=queries.vitals(session, days if hours >= 24 else 1), errors=queries.errors(session, "open", 30))


@router.get("/errors", response_model=list[dict[str, Any]])
def error_groups(ctx: CurrentAdmin, session: AdminDb, status: str = Query(default="open", pattern="^(open|resolved|ignored|all)$")) -> list[dict[str, Any]]:
    return queries.errors(session, status, 100)


@router.post("/errors/{fingerprint}", response_model=dict[str, str])
def set_error_status(fingerprint: str, data: InsightErrorStatusIn, ctx: OwnerAdmin, session: AdminDb) -> dict[str, str]:
    group = session.scalars(select(ErrorGroup).where(ErrorGroup.fingerprint == fingerprint)).first()
    if group is None:
        raise NotFound("Error group not found.")
    group.status = data.status
    record(session, ctx.admin, "insights.error_status", target_type="error_group", target_id=fingerprint, ip=ctx.ip, metadata={"status": data.status})
    return {"status": data.status}


@router.get("/alerts", response_model=list[InsightAlertOut])
def alert_list(ctx: CurrentAdmin, session: AdminDb, days: int = Query(default=30, ge=1, le=180)) -> list[InsightAlertOut]:
    return [InsightAlertOut(**a) for a in queries.alerts(session, days)]


@router.post("/alerts/{alert_id}/resolve", response_model=InsightAlertOut)
def resolve_alert(alert_id: int, ctx: OwnerAdmin, session: AdminDb) -> InsightAlertOut:
    from datetime import UTC

    row = session.get(InsightAlert, alert_id)
    if row is None:
        raise NotFound("Alert not found.")
    if row.resolved_at is None:
        row.resolved_at = datetime.now(UTC)
        record(session, ctx.admin, "insights.alert_resolved", target_type="insight_alert", target_id=alert_id, ip=ctx.ip)
    return InsightAlertOut(**next(a for a in queries.alerts(session, 180) if a["id"] == alert_id))


@router.get("/settings", response_model=InsightSettings)
def get_settings(ctx: CurrentAdmin, session: AdminDb) -> InsightSettings:
    return InsightSettings(alerts=alert_rules.thresholds(session))


@router.put("/settings", response_model=InsightSettings)
def put_settings(data: InsightSettings, ctx: OwnerAdmin, session: AdminDb) -> InsightSettings:
    clean = {k: float(v) for k, v in data.alerts.items() if k in alert_rules.DEFAULTS and v >= 0}
    stored = dict(platform.get(session, "insights") or {})
    stored["alerts"] = clean
    platform.put(session, "insights", stored)
    record(session, ctx.admin, "insights.settings", target_type="platform", target_id="insights", ip=ctx.ip)
    return InsightSettings(alerts=alert_rules.thresholds(session))


@router.get("/export", response_class=Response)
def export(ctx: CurrentAdmin, session: AdminDb, metric: str = Query(max_length=60), days: int = Days) -> Response:
    """Every day and dimension of one metric as CSV (for spreadsheets)."""
    from ..models import KpiDaily

    if not metric.replace(".", "").replace("_", "").isalnum():
        raise ValidationFailed("Unknown metric.")
    start, end = queries.period(days)
    rows = session.execute(select(KpiDaily.day, KpiDaily.dimension, KpiDaily.value).where(KpiDaily.metric == metric, KpiDaily.day >= start, KpiDaily.day <= end)
                           .order_by(KpiDaily.day, KpiDaily.dimension)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["day", "metric", "dimension", "value"])
    for day, dimension, value in rows:
        writer.writerow([day.isoformat(), metric, dimension, value])
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="insights-{metric}-{days}d.csv"'})
