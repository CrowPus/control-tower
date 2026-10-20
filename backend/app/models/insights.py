"""Insights: counts and timings about how the install and the business are doing. No person
and no content in any of these tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class KpiDaily(Base, IdMixin):
    """One number per day (the owner's time zone), metric and dimension: the history that
    survives purges."""

    __tablename__ = "kpi_daily"
    __table_args__ = (UniqueConstraint("day", "metric", "dimension", name="uq_kpi_daily"), Index("ix_kpi_daily_metric", "metric", "day"))

    day: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    dimension: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class RequestMetric(Base, IdMixin):
    """Requests per period, app, route template and status class, with a latency histogram.

    Each server process writes its own rows per minute; the nightly task folds minutes into
    hours and hours into days. Readers always sum."""

    __tablename__ = "request_metrics"
    __table_args__ = (Index("ix_request_metrics_bucket", "granularity", "bucket"),)

    granularity: Mapped[str] = mapped_column(String(6), nullable=False)  # minute | hour | day
    bucket: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    app: Mapped[str] = mapped_column(String(16), nullable=False)
    route: Mapped[str] = mapped_column(String(120), nullable=False)
    status_class: Mapped[str] = mapped_column(String(3), nullable=False)  # 2xx | 3xx | 4xx | 5xx
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sum_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Counts per latency bucket (upper bounds in ms, see services/insights/collector.BUCKETS).
    histogram: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)


class TelemetryEvent(Base, IdMixin):
    """A page-speed measurement, timing or error from a browser or the phone app (30 days)."""

    __tablename__ = "telemetry_events"
    __table_args__ = (Index("ix_telemetry_events_kind", "kind", "created_at"), Index("ix_telemetry_events_created", "created_at"))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    app: Mapped[str] = mapped_column(String(16), nullable=False)  # member_web | mobile | employer | website
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # vital | timing | error | crash
    name: Mapped[str] = mapped_column(String(40), nullable=False)  # LCP, INP, CLS, TTFB, app_start, screen, api, ...
    page: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    device: Mapped[str] = mapped_column(String(10), nullable=False, default="")  # phone | tablet | desktop
    os: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    browser: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class ErrorGroup(Base, IdMixin):
    """Errors and crashes grouped by fingerprint: what breaks, where, since when, how often."""

    __tablename__ = "error_groups"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_error_groups_fingerprint"), Index("ix_error_groups_last", "last_seen"))

    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    app: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # error | crash
    error_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    page: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    versions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # open | resolved | ignored


class UptimeCheck(Base, IdMixin):
    """One request to a public address from the worker (30 days)."""

    __tablename__ = "uptime_checks"
    __table_args__ = (Index("ix_uptime_checks_target", "target", "checked_at"),)

    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    target: Mapped[str] = mapped_column(String(40), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class InsightAlert(Base, IdMixin):
    """An incident found by the alert rules: open until the rule clears."""

    __tablename__ = "insight_alerts"
    __table_args__ = (Index("ix_insight_alerts_open", "resolved_at", "rule"),)

    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="warning")  # warning | critical
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
