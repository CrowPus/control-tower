"""Nia says only what really happened (2026-10-10: she told the owner changes were made and agents were asked when nothing
had happened — her admin changes had failed and her directive still waited for the owner's OK).

What her proposals and directives really became is put in front of her before every answer, and the rules say: "done"
only when a tool result shows it; a proposal waits for the owner; a directive waits for the owner's OK; after the owner
says they approved, look before confirming, and say plainly when something failed and why."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import OwnerDirective, TeamApproval

RECENT = timedelta(days=3)
SHOWN = 12

RULES = (
    "TRUTH (the owner checks everything you say):\n"
    "- Say something is done, set, sent, changed or approved ONLY when a tool result in this conversation, or YOUR RECENT PROPOSALS "
    "below, shows it. Never from what you meant to do.\n"
    "- A proposal is not done: say \"I proposed it — it waits for your Approve in Approvals (#id)\". A directive is not done either: "
    "it reaches the agents only after the owner confirms it.\n"
    "- When the owner says they approved or confirmed something, check with proposal_status before you confirm it, and tell them "
    "what really happened: done, still waiting, or failed with the reason (then offer a corrected proposal).\n"
    "- Navigating or showing a page: say you opened it only when the page tool answered.\n"
    "- If you could not do something (no tool, refused, failed), say so in the first sentence.")


def _reason(row: TeamApproval) -> str:
    result = row.result or {}
    answer = result.get("answer") if isinstance(result, dict) else None
    error = result.get("error") if isinstance(result, dict) else None
    if isinstance(answer, dict):
        err = answer.get("error") or {}
        fields = "; ".join(f"{f.get('field')}: {f.get('message')}" for f in (err.get("fields") or [])[:4] if isinstance(f, dict))
        return f"{err.get('message', '')} {fields}".strip()[:300]
    return str(error or answer or "")[:300]


def recent(session: Session, agent: str = "nia") -> dict[str, list[dict[str, Any]]]:
    """The agent's proposals and Nia's directives of the last days, with what became of them."""
    since = datetime.now(UTC) - RECENT
    proposals = [{"id": r.id, "what": r.title, "type": r.action_type, "status": r.status, **({"why_failed": _reason(r)} if r.status == "failed" else {}),
                  "decided_at": r.decided_at.isoformat() if r.decided_at else None}
                 for r in session.execute(select(TeamApproval).where(TeamApproval.agent == agent, TeamApproval.created_at >= since)
                                          .order_by(TeamApproval.id.desc()).limit(SHOWN)).scalars()]
    directives = [{"id": d.id, "text": d.text[:200], "status": d.status, "agents": d.agents}
                  for d in session.execute(select(OwnerDirective).where(OwnerDirective.created_at >= since).order_by(OwnerDirective.id.desc()).limit(SHOWN)).scalars()]
    return {"proposals": proposals, "directives": directives}


def block(session: Session) -> str:
    seen = recent(session)
    return (RULES + "\n\nYOUR RECENT PROPOSALS (what really became of them; pending = waiting for the owner, executed = done, failed = NOT done): "
            + (str(seen["proposals"]) if seen["proposals"] else "none")
            + "\nYOUR RECENT DIRECTIVES (proposed = waiting for the owner's OK, active = the agents have it): " + (str(seen["directives"]) if seen["directives"] else "none"))
