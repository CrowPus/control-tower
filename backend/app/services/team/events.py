"""Handoffs between agents: agents never call each other;
they write events, and each agent reads the types it subscribes to."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import TeamAgentState, TeamEvent
from . import registry

# Every agent hears these, whatever it subscribes to.
BROADCAST = ("approval.decided", "approval.executed", "owner.directive")


def emit(session: Session, type_: str, source: str, payload: dict[str, Any] | None = None) -> TeamEvent:
    event = TeamEvent(type=type_[:60], source=source[:40], payload=payload or {}, created_at=datetime.now(UTC))
    session.add(event)
    session.flush()
    return event


def state(session: Session, agent: str) -> TeamAgentState:
    row = session.execute(select(TeamAgentState).where(TeamAgentState.agent == agent)).scalar_one_or_none()
    if row is None:
        row = TeamAgentState(agent=agent, event_cursor=0, notes=[], autonomy_offers=[])
        session.add(row)
        session.flush()
    return row


def _wanted(agent: registry.Agent, event: TeamEvent) -> bool:
    if event.source == agent.key:
        return False
    if event.type == "owner.directive":
        return agent.key in (event.payload.get("agents") or [])
    if event.type in BROADCAST:
        return event.payload.get("agent") == agent.key
    if event.payload.get("to"):
        # Addressed to one agent (a Marketing Lead's brief): only that one hears it.
        return event.type in agent.subscribes and event.payload.get("to") == agent.key
    return event.type in agent.subscribes


def unseen(session: Session, agent_key: str, limit: int) -> tuple[list[TeamEvent], int]:
    """The events this agent has not read yet (oldest first), and how far reading them moves
    its cursor (past events meant for others, up to the last one returned)."""
    agent = registry.get(agent_key)
    if agent is None:
        return [], 0
    cursor = state(session, agent_key).event_cursor
    types = set(agent.subscribes) | set(BROADCAST)
    rows = list(session.execute(
        select(TeamEvent).where(TeamEvent.id > cursor, TeamEvent.type.in_(types)).order_by(TeamEvent.id).limit(limit * 4)
    ).scalars())
    wanted = [e for e in rows if _wanted(agent, e)]
    if len(wanted) > limit:
        return wanted[:limit], wanted[limit - 1].id
    return wanted, rows[-1].id if rows else cursor


def mark_seen(session: Session, agent_key: str, up_to: int) -> None:
    row = state(session, agent_key)
    if up_to > row.event_cursor:
        row.event_cursor = up_to
