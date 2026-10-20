"""Outside agents: agents from other providers — Claude, ChatGPT, Gemini … — that work in
the company like the team, with a desk, a credential the owner issues, the tools the owner ticks, and Approvals for every
change. Connected ones bring their own model and talk to the Agent Gateway; hosted ones run on the install's server with the
owner's provider key.

Nothing here is a member's data. Credentials and tokens are kept only as hashes; provider keys only encrypted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, UTCDateTime


class OutsideAgent(Base, IdMixin):
    __tablename__ = "outside_agents"

    # "ext_" + a short name: the agent's key everywhere (Approvals, runs, events, the office).
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    job: Mapped[str] = mapped_column(Text, nullable=False, default="")
    department: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    # connected (its own model, through the Agent Gateway) | hosted (the install runs it with the owner's provider key)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="connected")
    # anthropic | openai | google | other — for a connected one only a label ("Claude Code", "ChatGPT" …)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    client_label: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    provider_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    desk: Mapped[int | None] = mapped_column(Integer)
    # draft (no credential yet) | waiting (credential issued, never connected) | active | paused | revoked
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    calls_per_day: Mapped[int | None] = mapped_column(Integer)
    tokens_per_day: Mapped[int | None] = mapped_column(Integer)
    # Hosted: when it works (the team's schedule shape: {"times": ["07:00"], "weekdays": [0, 3], "every_minutes": null}).
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ip_allowlist: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    credential_prefix: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    credential_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    credential_issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OutsideAgentCall(Base, IdMixin):
    """Every call an outside agent makes (gateway or hosted run): the audit."""

    __tablename__ = "outside_agent_calls"
    __table_args__ = (Index("ix_outside_agent_calls_agent", "agent_id", "at"),)

    agent_id: Mapped[int] = mapped_column(ForeignKey("outside_agents.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    via: Mapped[str] = mapped_column(String(12), nullable=False, default="gateway")  # gateway | hosted
    method: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    tool: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    args: Mapped[str] = mapped_column(Text, nullable=False, default="")  # the inputs, cut to a few thousand characters
    result_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OutsideAgentMessage(Base, IdMixin):
    """What the owner (or Nia for the owner) tells an outside agent; it reads them with `my_messages`."""

    __tablename__ = "outside_agent_messages"
    __table_args__ = (Index("ix_outside_agent_messages_agent", "agent_id", "created_at"),)

    agent_id: Mapped[int] = mapped_column(ForeignKey("outside_agents.id", ondelete="CASCADE"), nullable=False)
    sender: Mapped[str] = mapped_column(String(40), nullable=False, default="owner")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OutsideOAuthClient(Base, IdMixin):
    """A connector (Claude.ai, ChatGPT …) registered itself for OAuth sign-in (dynamic client registration)."""

    __tablename__ = "outside_oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OutsideOAuthGrant(Base, IdMixin):
    """A sign-in code, access token or refresh token for one agent through one connector (hash only)."""

    __tablename__ = "outside_oauth_grants"
    __table_args__ = (Index("ix_outside_oauth_grants_hash", "token_hash", unique=True),)

    agent_id: Mapped[int] = mapped_column(ForeignKey("outside_agents.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # code | access | refresh
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
