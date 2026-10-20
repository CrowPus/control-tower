"""Actions the agents propose.

The model proposes; deterministic code acts (AGENTS.md invariant 1). An agent's proposal is
a typed record; nothing happens until the owner approves it (L1, L3), or, for action types
the owner let run alone, within the daily limit the owner set (L2). Approved actions are
carried out by the worker (`execute_due`) with the executor registered for their type.

Earned autonomy: approvals in a row without an edit are counted per agent and action type;
at the owner's number the admin app offers L2. The owner answers; nothing changes alone.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import TeamApproval, TeamPlaybook
from . import config, events, registry

Executor = Callable[[Session, TeamApproval, dict[str, Any]], dict[str, Any]]
EXECUTORS: dict[str, Executor] = {}


class ProposalRefused(ValueError):
    """The proposal breaks the agent's design or the owner's limits; the agent is told why."""


# What one proposal is about, per action type: the payload key naming its target ("" = one
# open proposal of that type per agent at a time). A second proposal for the same target while
# the first still waits is refused, so an agent that runs again does not repeat itself.
# admin.change is not listed: two different changes may well wait side by side.
TARGET: dict[str, str] = {
    "playbook.change": "",
    "product.idea": "title",
    "members.tip": "group",
    "marketing.plan": "",
    "source.add": "url",
    "research.focus": "",
    "listing.verdict": "listing_id",
    "showcase.list": "country",
    "listing.complete": "listing_id",
}


# Action types whose newer proposal replaces the one still waiting (it holds everything the older one did).
REPLACES = ("playbook.change", "marketing.plan", "research.focus", "showcase.list")

# Checks made when an agent proposes (the agent hears what is wrong at once, not when the owner approves).
CHECKS: dict[str, Callable[[Session, dict[str, Any]], list[str]]] = {}

# Checks made again on every waiting proposal (team.dispatch, each minute) and shown on its Approvals card: a proposal
# that became a duplicate (another one about the same company was carried out, a letter went out) is withdrawn, so the
# owner never approves the same thing twice.
RECHECKS: dict[str, Callable[[Session, TeamApproval], list[str]]] = {}


def recheck(session: Session, row: TeamApproval) -> list[str]:
    fn = RECHECKS.get(row.action_type)
    return fn(session, row) if fn else []


def withdraw_stale(session: Session) -> list[int]:
    """Withdraw waiting proposals that can no longer be carried out as proposed; the agent hears why."""
    gone: list[int] = []
    for row in session.execute(select(TeamApproval).where(TeamApproval.status == "pending", TeamApproval.action_type.in_(tuple(RECHECKS))).order_by(TeamApproval.id)).scalars():
        problems = recheck(session, row)
        if not problems:
            continue
        row.status, row.decided_at = "withdrawn", datetime.now(UTC)
        row.decision_note = ("Withdrawn by the duplicate check: " + " ".join(problems))[:2000]
        events.emit(session, "approval.decided", "system", {"agent": row.agent, "approval_id": row.id, "action_type": row.action_type, "decision": "withdrawn",
                                                           "edited": False, "note": row.decision_note})
        gone.append(row.id)
    session.flush()
    return gone


def open_duplicate(session: Session, agent: str, action_type: str, payload: dict[str, Any]) -> TeamApproval | None:
    """An earlier proposal of this agent about the same thing that still waits (or is approved and not yet carried out)."""
    if action_type not in TARGET:
        return None
    key = TARGET[action_type]
    rows = session.execute(
        select(TeamApproval).where(TeamApproval.agent == agent, TeamApproval.action_type == action_type, TeamApproval.status.in_(("pending", "approved")))
        .order_by(TeamApproval.id)
    ).scalars()
    for row in rows:
        if not key or str(row.payload.get(key)) == str(payload.get(key)):
            return row
    return None


def executor(action_type: str) -> Callable[[Executor], Executor]:
    def register(fn: Executor) -> Executor:
        EXECUTORS[action_type] = fn
        return fn

    return register


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _count_today(session: Session, agent: str, action_type: str, *, alone: bool) -> int:
    stmt = select(func.count()).select_from(TeamApproval).where(
        TeamApproval.agent == agent, TeamApproval.action_type == action_type, TeamApproval.created_at >= _day_start(datetime.now(UTC)),
    )
    if alone:
        stmt = stmt.where(TeamApproval.level == "L2", TeamApproval.decided_by_admin_id.is_(None), TeamApproval.status.in_(("approved", "executed", "failed")))
    return int(session.execute(stmt).scalar() or 0)


def propose(
    session: Session,
    *,
    agent: str,
    action_type: str,
    title: str,
    payload: dict[str, Any],
    reason: str = "",
    expected_effect: str = "",
    evidence: list[dict[str, Any]] | None = None,
    group_key: str = "",
    run_id: int | None = None,
) -> TeamApproval:
    spec = registry.get(agent)
    if spec is None and agent.startswith("ext_"):
        from ..outside import core as outside

        spec = outside.spec_of(session, agent)
    if spec is None or action_type not in registry.actions_of(spec):
        raise ProposalRefused(f"{agent} cannot propose {action_type}.")
    if action_type not in EXECUTORS:
        raise ProposalRefused(f"{action_type} cannot be carried out yet.")
    check = CHECKS.get(action_type)
    problems = check(session, payload) if check else []
    if problems:
        raise ProposalRefused(" ".join(problems))
    earlier = open_duplicate(session, agent, action_type, payload)
    if earlier is not None and action_type in REPLACES and earlier.status == "pending":
        # A playbook change carries the whole new playbook: the newer one replaces the one still waiting.
        earlier.status, earlier.decision_note = "withdrawn", "Replaced by a newer proposal from the agent."
        earlier = None
    if earlier is not None:
        raise ProposalRefused(f"Already waiting as #{earlier.id} ({earlier.title}). Do not propose it again; the owner decides that one first.")
    mine = config.agent(session, agent)
    cap = mine.proposals_per_day.get(action_type)
    if cap is not None and _count_today(session, agent, action_type, alone=False) >= cap:
        raise ProposalRefused(f"Today's limit for {registry.ACTIONS[action_type].label} is reached ({cap}).")
    level = config.level_of(mine, action_type)
    alone = level == "L2" and _count_today(session, agent, action_type, alone=True) < (mine.l2_daily_limit.get(action_type) or 0)
    now = datetime.now(UTC)
    row = TeamApproval(
        agent=agent, action_type=action_type, level=level, title=title[:300], reason=reason, expected_effect=expected_effect,
        evidence=evidence or [], payload=payload, group_key=group_key[:80], run_id=run_id, result={}, created_at=now,
        # L2 within the owner's limit: approved by the rule, carried out by the worker, reported.
        status="approved" if alone else "pending",
        decided_at=now if alone else None,
        decision_note="Within the limit the owner set for running alone." if alone else "",
    )
    session.add(row)
    session.flush()
    return row


Decision = Literal["approve", "reject"]


def decide(session: Session, rows: list[TeamApproval], decision: Decision, *, admin_id: int, note: str = "", edited_payload: dict[str, Any] | None = None) -> list[TeamApproval]:
    """The owner's answer. Only pending proposals change; an edit is for one proposal at a time."""
    if edited_payload is not None and len(rows) != 1:
        raise ValueError("Edit one proposal at a time.")
    now = datetime.now(UTC)
    changed: list[TeamApproval] = []
    for row in rows:
        if row.status != "pending":
            continue
        row.status = "approved" if decision == "approve" else "rejected"
        row.decided_by_admin_id = admin_id
        row.decided_at = now
        row.decision_note = note[:2000]
        if decision == "approve" and edited_payload is not None and edited_payload != row.payload:
            row.edited_payload = edited_payload
        events.emit(session, "approval.decided", "owner", {"agent": row.agent, "approval_id": row.id, "action_type": row.action_type,
                                                          "decision": row.status, "edited": row.edited_payload is not None, "note": row.decision_note})
        changed.append(row)
    session.flush()
    for agent, action_type in {(r.agent, r.action_type) for r in changed}:
        _maybe_offer(session, agent, action_type)
    return changed


def streak(session: Session, agent: str, action_type: str) -> int:
    """Owner approvals in a row without an edit, newest first, up to the last edit or rejection."""
    rows = session.execute(
        select(TeamApproval.status, TeamApproval.edited_payload).where(
            TeamApproval.agent == agent, TeamApproval.action_type == action_type, TeamApproval.decided_by_admin_id.is_not(None),
        ).order_by(TeamApproval.decided_at.desc(), TeamApproval.id.desc()).limit(1000)
    ).all()
    count = 0
    for status, edited in rows:
        if status == "rejected" or edited is not None:
            break
        count += 1
    return count


def _maybe_offer(session: Session, agent: str, action_type: str) -> None:
    threshold = config.team(session).autonomy_offer_after
    if threshold is None or registry.ACTIONS[action_type].ceiling == "L3":
        return
    if config.level_of(config.agent(session, agent), action_type) != "L1":
        return
    state = events.state(session, agent)
    if action_type not in state.autonomy_offers and streak(session, agent, action_type) >= threshold:
        state.autonomy_offers = [*state.autonomy_offers, action_type]


def answer_offer(session: Session, agent: str, action_type: str, *, accept: bool, daily_limit: int | None) -> None:
    """The owner's answer to an autonomy offer. Accepting needs the owner's daily limit."""
    state = events.state(session, agent)
    if action_type not in state.autonomy_offers:
        raise ValueError("There is no such offer.")
    if accept:
        if daily_limit is None:
            raise ValueError("Set how many may run alone per day.")
        mine = config.agent(session, agent)
        mine.levels[action_type] = "L2"
        mine.l2_daily_limit[action_type] = daily_limit
        config.save_agent(session, agent, mine)
    state.autonomy_offers = [a for a in state.autonomy_offers if a != action_type]


def execute_due(session: Session, *, limit: int = 50) -> int:
    """Carry out approved actions (the worker, every minute)."""
    rows = list(session.execute(select(TeamApproval).where(TeamApproval.status == "approved").order_by(TeamApproval.id).limit(limit)).scalars())
    for row in rows:
        payload = row.edited_payload if row.edited_payload is not None else row.payload
        try:
            # A savepoint per action: a database error inside one executor rolls back only its own
            # work, so its failure can be recorded and the next action still runs.
            with session.begin_nested():
                result = EXECUTORS[row.action_type](session, row, payload)
            row.result = result
            row.status = "executed"
        except Exception as exc:  # noqa: BLE001 - one failed action must not stop the others
            row.result = {"error": f"{exc.__class__.__name__}: {exc}"[:500]}
            row.status = "failed"
        row.executed_at = datetime.now(UTC)
        events.emit(session, "approval.executed", "system", {"agent": row.agent, "approval_id": row.id, "action_type": row.action_type, "status": row.status})
    return len(rows)


# --- executors ------------------------------------------------------------------------------------
def latest_playbook(session: Session, agent: str) -> TeamPlaybook | None:
    return session.execute(select(TeamPlaybook).where(TeamPlaybook.agent == agent).order_by(TeamPlaybook.version.desc()).limit(1)).scalar_one_or_none()


def save_playbook(session: Session, agent: str, text: str, *, note: str, created_by: str) -> TeamPlaybook:
    current = latest_playbook(session, agent)
    row = TeamPlaybook(agent=agent, version=(current.version if current else 0) + 1, text=text, note=note[:300], created_by=created_by[:120], created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


@executor("playbook.change")
def _playbook_change(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("The new playbook is empty.")
    row = save_playbook(session, approval.agent, text, note=str(payload.get("note") or approval.title), created_by=f"approval #{approval.id}")
    return {"version": row.version}


@executor("admin.change")
def _admin_change(_session: Session, _approval: TeamApproval, _payload: dict[str, Any]) -> dict[str, Any]:
    """Never carried out here: the owner's own admin page sends it when the owner approves it on
    screen (services/team/admin_changes.py). Reaching this means it was approved another way."""
    raise RuntimeError("An admin change is carried out only when the owner approves it on screen in Approvals.")
