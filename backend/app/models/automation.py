from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .base import Base, IdMixin, TimestampMixin, UTCDateTime


class AgentTask(Base, IdMixin, TimestampMixin):
    """Durable work queue (ADR-001). Claimed atomically by a worker; retried with
    exponential back-off + jitter; dead-lettered after max attempts."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_claim", "status", "queue", "run_at", "priority"),
        Index("ix_agent_tasks_dedupe", "dedupe_key", unique=True),
        Index("ix_agent_tasks_created", "created_at"),
    )

    queue: Mapped[str] = mapped_column(String(40), nullable=False, default="default")
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")  # queued, running, succeeded, failed, dead, cancelled
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_by: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_error_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class ScheduledJob(Base, IdMixin, TimestampMixin):
    __tablename__ = "scheduled_jobs"

    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    queue: Mapped[str] = mapped_column(String(40), nullable=False, default="maintenance")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class AIUsage(Base, IdMixin):
    """Per-call AI consumption: what each call cost, for budgets and Admin → AI usage."""

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created", "created_at"),)

    agent_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_class: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # The provider's own words for a failure ("enum[4]: cannot be empty"), without
    # secrets. A class like client_error_400 alone hid a broken schema for days.
    error_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AICache(Base, IdMixin):
    """Cache of structured AI results keyed by (operation, content hash) — spec §76."""

    __tablename__ = "ai_cache"
    __table_args__ = (Index("ix_ai_cache_key", "cache_key", unique=True),)

    cache_key: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
