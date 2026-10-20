"""The agent team: the owner's agents that run the business.

Each agent's settings live in the platform settings (`agents`); these tables hold what the
agents did: runs, the events they hand to each other, the actions they propose for the
owner's approval, their working state and the versions of their playbooks.

Nothing here is a member's data. Later stages add prospects (companies and their contact
persons from legal notices); those get their own tables and retention.
"""

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


class TeamRun(Base, IdMixin):
    """One run of one agent: why it ran, what it used, what came out."""

    __tablename__ = "team_runs"
    __table_args__ = (Index("ix_team_runs_agent", "agent", "started_at"),)

    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)  # schedule | event | work (waiting work) | manual
    # running | done | failed | waiting (a setting is missing) | skipped (off, over budget)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Tool names and short results, never the model's hidden reasoning.
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    waiting_for: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")


class TeamEvent(Base, IdMixin):
    """A handoff: one agent (or the system) says something happened; subscribers react."""

    __tablename__ = "team_events"
    __table_args__ = (Index("ix_team_events_type", "type", "id"),)

    type: Mapped[str] = mapped_column(String(60), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)  # an agent, "system" or "owner"
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TeamApproval(Base, IdMixin):
    """An action an agent proposes. Pending until the owner decides (L1, L3), or done at
    once within the owner's limits (L2). Approved actions are carried out by the worker."""

    __tablename__ = "team_approvals"
    __table_args__ = (
        Index("ix_team_approvals_status", "status", "created_at"),
        Index("ix_team_approvals_agent", "agent", "action_type", "decided_at"),
    )

    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    level: Mapped[str] = mapped_column(String(3), nullable=False)  # L1 | L2 | L3
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_effect: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # What the owner changed before approving (null: approved as proposed).
    edited_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Proposals made together (a batch of letters) are approved together.
    group_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # pending | approved | rejected | withdrawn | executed | failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    decided_by_admin_id: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("team_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TeamAgentState(Base, IdMixin):
    """An agent's working state: how far it has read the events, when it last ran, and the
    short notes it keeps (lessons with their evidence; never a copy of personal data)."""

    __tablename__ = "team_agent_state"
    __table_args__ = (UniqueConstraint("agent", name="uq_team_agent_state"),)

    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # Action types the admin app offers to move to L2 (earned autonomy), until the owner answers.
    autonomy_offers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class TeamPlaybook(Base, IdMixin):
    """Every version of an agent's playbook: the owner's instructions on how it works."""

    __tablename__ = "team_playbooks"
    __table_args__ = (UniqueConstraint("agent", "version", name="uq_team_playbooks"),)

    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")  # an admin's e-mail or an approval
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TeamConversation(Base, IdMixin):
    """The owner talking with one agent (Admin → Team → agent → Chat). Nia keeps her own
    conversations (models/nia.py)."""

    __tablename__ = "team_conversations"
    __table_args__ = (Index("ix_team_conversations_agent", "agent", "updated_at"),)

    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    admin_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TeamMessage(Base, IdMixin):
    """One message in a conversation: the owner's (with the files attached to it) or the agent's
    answer (with the tools it used and the proposals it made)."""

    __tablename__ = "team_messages"
    __table_args__ = (Index("ix_team_messages_conversation", "conversation_id", "id"),)

    conversation_id: Mapped[int] = mapped_column(ForeignKey("team_conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)  # owner | agent
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachment_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    proposal_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TeamAttachment(Base, IdMixin):
    """A file the owner gave an agent in a conversation. Checked, scanned and stored here with
    its extracted text; deleted with the conversation."""

    __tablename__ = "team_attachments"
    __table_args__ = (Index("ix_team_attachments_conversation", "conversation_id"),)

    conversation_id: Mapped[int] = mapped_column(ForeignKey("team_conversations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The text the agent reads (tables as rows, PDFs as text); empty for images.
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scan: Mapped[str] = mapped_column(String(20), nullable=False)  # clean | test (no scanner in tests)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
