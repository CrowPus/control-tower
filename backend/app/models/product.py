"""The Product department: what the QA Runner's checks found each run, the
incidents the Engineer on Duty keeps, and the Product Analyst's ideas with what they changed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class QaRun(Base, IdMixin):
    """One run of the checks: every check with its result (kept 30 days)."""

    __tablename__ = "qa_runs"
    __table_args__ = (Index("ix_qa_runs_started", "started_at"),)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trigger: Mapped[str] = mapped_column(String(12), nullable=False, default="schedule")  # schedule | agent | owner
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slowest_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Incident(Base, IdMixin):
    """Something that hurts people, as the Engineer on Duty (or the owner) wrote it down: what is broken, since when,
    whom it affects, the likely cause, the fix, and a timeline of notes. status: open → monitoring → resolved."""

    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_status", "status", "started_at"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="major")  # critical | major | minor
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    # What it is about, so the same problem is not opened twice (e.g. "qa:Job board", "task:team.dispatch").
    key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    opened_by: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ProductIdea(Base, IdMixin):
    """A change to the product the Product Analyst proposed from the numbers, with the number it should move.
    accepted (the owner approved it; its baseline is measured) → shipped (the owner marks when it went live)
    → measured (the analyst read the same number after it) | dropped."""

    __tablename__ = "product_ideas"
    __table_args__ = (Index("ix_product_ideas_status", "status"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False, default="")
    idea: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # The Insights number it should move (services/insights/queries.KPIS key) and in which direction.
    metric: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    expect: Mapped[str] = mapped_column(String(4), nullable=False, default="up")  # up | down
    measure_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    baseline: Mapped[float | None] = mapped_column(Float)
    result: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # better | worse | no_change | too_little_data
    result_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="accepted")
    approval_id: Mapped[int | None] = mapped_column(Integer)
    shipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    measured_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
