"""Nia's journal: after a conversation goes quiet — typed or spoken — Nia writes down what
she and the owner did together, what was decided, what the owner said about themselves and what is still open, as
memories with a topic. So the next talk starts where the last one ended, even days later.

Worker task `nia.journal` (every 10 minutes): each conversation with new messages that has been quiet for QUIET_MINUTES
is read in chunks of CHUNK messages (oldest first) until it is caught up. Nia's and the team's AI budgets apply."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from ...core.logging import get_logger
from ...models import AgentTask, NiaConversation, NiaMessage
from ...workers.worker import task
from ..team import config, runner, tools
from . import memory

log = get_logger(__name__)

# Safety rails, not business values: when a conversation counts as quiet, how much is read at once, how many memories
# one chunk may give.
QUIET_MINUTES = 10
CHUNK = 120
MAX_PER_CHUNK = 6
KINDS = ("work", "person", "decision", "preference")

SYSTEM = (
    "You are Nia's journal. Read the conversation between the owner and Nia, their assistant, and write down what Nia must "
    "remember for the next talk. Answer with JSON only: a list of at most 6 objects {\"kind\", \"topic\", \"text\"}. kind: work (what was "
    "done together, the result, and what is still open — the most useful kind), person (something the owner said about themselves: name, "
    "family, how they work, what matters to them), decision (the owner decided something), preference (how the owner likes things). "
    "topic: one or two lowercase words (website, letters, finance, nia, team, marketing, a company's name …). text: one or two full "
    "sentences with the concrete facts (names, numbers, addresses, dates), written so it makes sense on its own months later. Skip small "
    "talk and anything already known (WHAT IS ALREADY REMEMBERED). Never write down personal data of members or of people at companies. "
    "If nothing is worth remembering, answer []."
)


def _parse(text: str) -> list[dict[str, str]]:
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("kind") in KINDS and str(item.get("text") or "").strip():
            out.append({"kind": str(item["kind"]), "topic": str(item.get("topic") or ""), "text": str(item["text"]).strip()[:1500]})
    return out[:MAX_PER_CHUNK]


def due(session: Any, now: datetime) -> list[int]:
    quiet_since = now - timedelta(minutes=QUIET_MINUTES)
    rows = session.execute(select(NiaConversation.id, NiaConversation.journaled_message_id, func.max(NiaMessage.id), func.max(NiaMessage.created_at))
                           .join(NiaMessage, NiaMessage.conversation_id == NiaConversation.id)
                           .group_by(NiaConversation.id, NiaConversation.journaled_message_id)).all()
    return [cid for cid, seen, last_id, last_at in rows if last_id > (seen or 0) and last_at is not None and last_at <= quiet_since]


def write(db: tools.DbFactory, conversation_id: int) -> int:
    """Read the next chunk of one conversation and keep what is worth remembering. Returns how many memories were kept."""
    from . import chat

    with db() as session:
        if chat.ready(session) or runner.budget_problem(session, "nia", runs=False):
            return 0
        conv = session.get(NiaConversation, conversation_id)
        if conv is None:
            return 0
        rows = list(session.execute(select(NiaMessage).where(NiaMessage.conversation_id == conversation_id, NiaMessage.id > conv.journaled_message_id)
                                    .order_by(NiaMessage.id).limit(CHUNK)).scalars())
        if not rows:
            return 0
        transcript = "\n".join(f"{'OWNER' if m.role == 'owner' else 'NIA'} ({m.created_at.date().isoformat()}): {m.text[:1500]}" for m in rows)
        known = memory.memory_block(session)
        tier = config.agent(session, "nia").tier or "fast"
        last_id = rows[-1].id
    usage: list[runner.Usage] = []
    try:
        text = runner.MODEL.converse(tier=tier, system=SYSTEM, prompt=f"WHAT IS ALREADY REMEMBERED:\n{known}\n\nTHE CONVERSATION:\n{transcript}",
                                     declarations=[], call_tool=lambda _n, _a: {}, before_round=lambda: None, on_usage=usage.append, max_rounds=1)
    except Exception as exc:  # noqa: BLE001 - tried again on the next run
        log.warning("nia journal failed: %s", exc.__class__.__name__)
        return 0
    from ...ai.client import record_usage

    kept = 0
    with db() as session:
        for u in usage:
            record_usage(model=u.model, operation="agent.nia.journal", agent_type="team", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                         latency_ms=u.latency_ms, success=True, session=session)
        for item in _parse(text):
            memory.add(session, kind=item["kind"], topic=item["topic"], text=item["text"], source=f"journal, conversation {conversation_id}")
            kept += 1
        conv = session.get(NiaConversation, conversation_id)
        if conv is not None:
            conv.journaled_message_id = max(conv.journaled_message_id, last_id)
    return kept


@task("nia.journal", queue="default")
def nia_journal(_job: AgentTask) -> dict[str, Any]:
    from ...db import transaction

    def db() -> Any:
        return transaction(write=True)

    with db() as session:
        ids = due(session, datetime.now(UTC))
    return {"conversations": len(ids), "kept": sum(write(db, cid) for cid in ids[:5])}
