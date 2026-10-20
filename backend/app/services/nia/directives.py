"""The owner's instructions to the team (§5.0 "Nia as the route"). Nia reads an instruction back
as a directive; it reaches the agents only after the owner confirms it. Agents report progress;
the owner closes it."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import OwnerDirective
from ..team import events, registry


class DirectiveRefused(ValueError):
    pass


def propose(session: Session, *, text: str, agents: list[str], goal: str = "", due_on: date | None = None, conversation_id: int | None = None) -> OwnerDirective:
    text = text.strip()
    if not text:
        raise DirectiveRefused("A directive needs its instruction.")
    from ...models import OutsideAgent

    outside = {k for (k,) in session.execute(select(OutsideAgent.key).where(OutsideAgent.status != "revoked"))}
    unknown = [a for a in agents if a not in registry.AGENTS and a not in outside]
    if unknown:
        raise DirectiveRefused(f"No such agent: {', '.join(unknown)}. Agents: {', '.join(registry.AGENTS)}.")
    if not agents:
        raise DirectiveRefused("Say which agents it is for.")
    row = OwnerDirective(text=text[:4000], agents=sorted(set(agents)), goal=goal.strip()[:2000], due_on=due_on, status="proposed", reports=[],
                         conversation_id=conversation_id, created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def confirm(session: Session, row: OwnerDirective) -> OwnerDirective:
    if row.status != "proposed":
        raise DirectiveRefused("Only a proposed directive can be confirmed.")
    row.status, row.confirmed_at = "active", datetime.now(UTC)
    events.emit(session, "owner.directive", "owner", {"directive_id": row.id, "agents": row.agents, "text": row.text, "goal": row.goal,
                                                    "due_on": row.due_on.isoformat() if row.due_on else None})
    return row


def close(session: Session, row: OwnerDirective, *, status: str) -> OwnerDirective:
    if status not in ("done", "cancelled"):
        raise DirectiveRefused("A directive is closed as done or cancelled.")
    if row.status in ("done", "cancelled"):
        raise DirectiveRefused("This directive is already closed.")
    row.status, row.closed_at = status, datetime.now(UTC)
    return row


def report(session: Session, row: OwnerDirective, *, agent: str, text: str, done: bool = False) -> None:
    if row.status != "active":
        raise DirectiveRefused("This directive is not active.")
    if agent not in row.agents and agent != "nia":
        raise DirectiveRefused("This directive is not for you.")
    entry: dict[str, Any] = {"agent": agent, "text": text.strip()[:2000], "done": done, "at": datetime.now(UTC).isoformat()}
    row.reports = [*row.reports, entry][-100:]


def active_for(session: Session, agent: str) -> list[OwnerDirective]:
    rows = session.execute(select(OwnerDirective).where(OwnerDirective.status == "active").order_by(OwnerDirective.id)).scalars()
    return [r for r in rows if agent in r.agents or agent == "nia"]
