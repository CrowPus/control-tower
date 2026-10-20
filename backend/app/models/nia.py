"""The owner's Nia: conversations, the memory the owner can see
and edit, and the owner's directives to the team. No member's personal data is copied here;
Nia looks things up through its tools when asked."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime

# person: about the owner (who they are, how they like to work); work: what Nia and the owner did together and what is
# still open (written by Nia's journal after each conversation and by the browser after each task).
MEMORY_KINDS = ("person", "decision", "preference", "work", "lesson", "fact")


class NiaConversation(Base, IdMixin):
    __tablename__ = "nia_conversations"

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    admin_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # The last message Nia's journal has written down as work memories (services/nia/journal.py).
    journaled_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class NiaMessage(Base, IdMixin):
    __tablename__ = "nia_messages"
    __table_args__ = (Index("ix_nia_messages_conversation", "conversation_id", "id"),)

    conversation_id: Mapped[int] = mapped_column(ForeignKey("nia_conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)  # owner | nia
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class NiaMemory(Base, IdMixin):
    """What Nia remembers: the owner's decisions and preferences, lessons with their evidence,
    facts about the business. The owner sees, corrects and deletes every one."""

    __tablename__ = "nia_memories"
    __table_args__ = (Index("ix_nia_memories_kind", "kind", "active"), Index("ix_nia_memories_topic", "topic", "active"))

    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    # What it is about, in one or two words ("website", "marketing", "finance", "Alex") — the branches of the memory tree.
    topic: Mapped[str] = mapped_column(String(60), nullable=False, default="", server_default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OwnerDirective(Base, IdMixin):
    """An instruction from the owner to the team, read back by Nia and confirmed by the owner."""

    __tablename__ = "owner_directives"
    __table_args__ = (Index("ix_owner_directives_status", "status", "id"),)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    agents: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_on: Mapped[date | None] = mapped_column(Date)
    # proposed (Nia read it back) | active (the owner confirmed) | done | cancelled
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="proposed")
    reports: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("nia_conversations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class NiaMeeting(Base, IdMixin):
    """A meeting with Nia the owner scheduled (§5.0 "Meetings and presentations"): Nia prepares a
    deck from live data before it, presents it by voice, and writes the minutes after it."""

    __tablename__ = "nia_meetings"
    __table_args__ = (Index("ix_nia_meetings_starts", "starts_at"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    agenda: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prepare_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat_weekly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # scheduled | preparing | prepared | held | cancelled
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="scheduled")
    # The deck: slides with their data frozen when they were made, and what Nia says to each.
    deck: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    deck_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prepared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("nia_conversations.id", ondelete="SET NULL"))
    minutes_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
