"""Trust, Money and Marketplace: the agents' notes on what waits for the
owner (a company or job to check, a report, a dispute), the payment reminders that went out, the monthly finance
close, and what the agent advised."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime

# The kinds of things agents review for the owner; a plugin adds its own.
REVIEW_KINDS: tuple[str, ...] = ()


class AgentReview(Base, IdMixin):
    """An agent's pre-check of one thing that waits for the owner's decision: what it checked, what it found and what
    it recommends. One per thing (a newer check replaces the older one); internal, never shown to the company.
    `basis` says what state of the thing it read, so a note on something that changed since is marked as outdated."""

    __tablename__ = "agent_reviews"
    __table_args__ = (UniqueConstraint("kind", "subject_id", name="uq_agent_reviews_subject"),)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # REVIEW_KINDS
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    # approve | ask | reject | watch (a company or job); act | dismiss (a report); accept | reject (a dispute)
    recommendation: Mapped[str] = mapped_column(String(12), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{"check": "Privacy policy", "ok": true, "detail": "…", "source": "https://…"}]
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    basis: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    run_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

