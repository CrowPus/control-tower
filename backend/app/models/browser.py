"""Nia's browser: one browsing session the owner started (by voice, chat or on the Nia
page) with its goal and state, and every step in it — Nia's turns (what the model asked for and what happened), the
moments Nia asked the owner, and what the owner did when taking over."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime

# idle: Nia finished the task and the window stays open (the owner watches, clicks, or gives the next task in it).
ACTIVE = ("running", "asking", "owner", "paused", "idle")


class BrowserSession(Base, IdMixin):
    """status: running (Nia works) → asking (waits for the owner's yes) | owner (the owner has the browser) | paused
    (nobody watching, or the owner paused) → done | stopped | failed."""

    __tablename__ = "browser_sessions"
    __table_args__ = (Index("ix_browser_sessions_status", "status", "updated_at"), UniqueConstraint("key", name="uq_browser_sessions_key"))

    # The session's name in the browser service (random, never an id that can be guessed).
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    # cloud: the install's own browser on the server; pc: Nia's own Chrome window on the owner's computer (NIA_DESKTOP.md).
    place: Mapped[str] = mapped_column(String(8), nullable=False, default="cloud", server_default="cloud")
    admin_id: Mapped[int | None] = mapped_column(Integer)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    start_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # The action Nia wants to take and waits for the owner's yes on: {"name", "args", "question"}.
    pending: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Turns Nia took (the next turn's number), what it found, its cost.
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    watched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BrowserStep(Base, IdMixin):
    """kind: turn (Nia: the model's calls and what happened — the history the model sees again), ask (Nia asked),
    answer (the owner answered), owner (the owner's own click or key), note (what Control Tower says, e.g. refused)."""

    __tablename__ = "browser_steps"
    __table_args__ = (Index("ix_browser_steps_session", "session_id", "id"),)

    session_id: Mapped[int] = mapped_column(ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False)
    turn: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # What it says, for the owner ("Clicks „Weiter“ on elster.de").
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A turn: the model's parts (function calls and words) and the response to each call.
    calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    responses: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # The screen after a turn (PNG, kept for the last turns the model sees) or at the moment Nia asked (JPEG, kept).
    screenshot: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
