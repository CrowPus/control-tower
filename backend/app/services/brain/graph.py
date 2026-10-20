"""The team's brain: everything the agents know and did as one graph.

Nodes are the agents and their departments, the handoffs (event types) between them, their tools, the proposals the
agents made, the notes they keep, Nia's memories with their topics and the owner's directives. Links say how they hang
together: who sends what to whom, who proposed what, which note or memory mentions which agent or proposal.

Built from the database each time it is asked (nothing is stored twice). No person ever becomes a node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    NiaMemory,
    OwnerDirective,
    TeamAgentState,
    TeamApproval,
    TeamEvent,
)
from ..team import config, registry

KINDS = ("agent", "department", "event", "tool", "proposal", "note", "memory", "topic", "directive")

# What Nia remembers that the other agents read too: the owner's rules and the lessons and facts of the business —
# not who the owner is as a person, nor Nia's own diary of the work she did with the owner.
SHARED_MEMORY_KINDS = ("decision", "preference", "lesson", "fact")

# Names shorter than this are not looked for in notes and memories (too many false matches).
MIN_NAME = 4
_REF = re.compile(r"#(\d+)")


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)

    def node(self, id_: str, node_kind: str, label: str, /, *, text: str = "", at: datetime | None = None, **detail: Any) -> str:
        if id_ not in self.nodes:
            self.nodes[id_] = {"id": id_, "kind": node_kind, "label": label[:120], "text": text[:2000], "at": at.isoformat() if at else None,
                               "detail": {k: v for k, v in detail.items() if v not in (None, "", [], {})}}
        return id_

    def link(self, source: str, target: str, kind: str, weight: int = 1) -> None:
        if source == target or source not in self.nodes or target not in self.nodes:
            return
        key = (source, target, kind)
        if key in self.links:
            self.links[key]["weight"] += weight
        else:
            self.links[key] = {"source": source, "target": target, "kind": kind, "weight": weight}

    def as_dict(self) -> dict[str, Any]:
        degree: dict[str, int] = {}
        for s, t, _k in self.links:
            degree[s] = degree.get(s, 0) + 1
            degree[t] = degree.get(t, 0) + 1
        nodes = [{**n, "degree": degree.get(n["id"], 0)} for n in self.nodes.values()]
        counts = {k: 0 for k in KINDS}
        for n in nodes:
            counts[n["kind"]] = counts.get(n["kind"], 0) + 1
        return {"nodes": nodes, "links": list(self.links.values()), "counts": counts}


def build(session: Session, *, days: int | None = None, for_agent: str | None = None) -> Graph:
    """The whole brain; `days` keeps proposals and events of that many days (None: all).
    `for_agent` (an agent reading the brain in its run) leaves out what only Nia and the owner read: memories
    about the owner as a person and Nia's diary of their work together."""
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    g = Graph()

    # --- the team ---------------------------------------------------------------------------------------------
    event_counts: dict[str, int] = {}
    stmt = select(TeamEvent.type, TeamEvent.source)
    if since:
        stmt = stmt.where(TeamEvent.created_at >= since)
    sent_by: dict[tuple[str, str], int] = {}
    for type_, source in session.execute(stmt).all():
        event_counts[type_] = event_counts.get(type_, 0) + 1
        sent_by[(source, type_)] = sent_by.get((source, type_), 0) + 1

    states = {s.agent: s for s in session.execute(select(TeamAgentState)).scalars()}
    names: list[tuple[str, str]] = []  # (name lower, node id) for finding mentions
    for agent in registry.AGENTS.values():
        status, waiting = config.status(session, agent.key)
        mine = config.agent(session, agent.key)
        state = states.get(agent.key)
        a = g.node(f"agent:{agent.key}", "agent", agent.name, text=agent.purpose, status=status, waiting=waiting, department=agent.department,
                   kpi=agent.kpi, last_run=state.last_run_at.isoformat() if state and state.last_run_at else None, key=agent.key)
        if len(agent.name) >= MIN_NAME:
            names.append((agent.name.lower(), a))
        d = g.node(f"department:{agent.department}", "department", agent.department)
        g.link(a, d, "works in")
        for type_ in agent.emits:
            e = g.node(f"event:{type_}", "event", type_, sent=event_counts.get(type_, 0))
            g.link(a, e, "sends", max(1, sent_by.get((agent.key, type_), 0)))
        for type_ in agent.subscribes:
            e = g.node(f"event:{type_}", "event", type_, sent=event_counts.get(type_, 0))
            g.link(e, a, "wakes")
        granted = set(mine.tools)
        for tool in registry.tools_of(agent):
            if tool in granted:
                g.link(a, g.node(f"tool:{tool}", "tool", tool.replace("_", " ")), "uses")
        if state is not None:
            for i, note in enumerate(state.notes):
                at = _when(note.get("at"))
                n = g.node(f"note:{agent.key}:{i}", "note", _first_words(str(note.get("text", ""))), text=str(note.get("text", "")), at=at,
                           evidence=str(note.get("evidence", "")), agent=agent.name, run_id=note.get("run_id"))
                g.link(a, n, "noted")

    # --- what the agents proposed ---------------------------------------------------------------------------------
    p_stmt = select(TeamApproval).order_by(TeamApproval.id)
    if since:
        p_stmt = p_stmt.where(TeamApproval.created_at >= since)
    for p in session.execute(p_stmt).scalars():
        pid = g.node(f"proposal:{p.id}", "proposal", f"#{p.id} {p.title}", text=p.reason, at=p.created_at, proposal_id=p.id,
                     action_type=p.action_type, level=p.level, status=p.status, expected_effect=p.expected_effect, owner_note=p.decision_note)
        g.link(f"agent:{p.agent}", pid, "proposed")

    # --- Nia's memory and the owner's directives ------------------------------------------------------------------
    nia = "agent:nia"
    for m in session.execute(select(NiaMemory).where(NiaMemory.active.is_(True))).scalars():
        if for_agent and for_agent != "nia" and m.kind not in SHARED_MEMORY_KINDS:
            continue
        mid = g.node(f"memory:{m.id}", "memory", _first_words(m.text), text=m.text, at=m.created_at, kind=m.kind, topic=m.topic,
                     evidence=m.evidence, source=m.source, memory_id=m.id)
        topic = m.topic or "no topic"
        t = g.node(f"topic:{topic}", "topic", topic)
        g.link(t, mid, "holds")
        g.link(nia, t, "remembers")
    for row in session.execute(select(OwnerDirective).where(OwnerDirective.status.in_(("proposed", "active", "done")))).scalars():
        did = g.node(f"directive:{row.id}", "directive", _first_words(row.text), text=row.text, at=row.created_at, status=row.status, goal=row.goal,
                     due_on=row.due_on.isoformat() if row.due_on else None, reports=len(row.reports or []), directive_id=row.id)
        g.link(nia, did, "passed on")
        for key in row.agents or []:
            g.link(did, f"agent:{key}", "for")

    # --- mentions: the threads between what one agent wrote and what another works on --------------------------
    _link_mentions(g, names)
    return g


def _link_mentions(g: Graph, names: list[tuple[str, str]]) -> None:
    writings = [n for n in g.nodes.values() if n["kind"] in ("note", "memory", "directive", "proposal") and n["text"]]
    if not names or not writings:
        return
    names.sort(key=lambda x: -len(x[0]))
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n, _ in names) + r")\b", re.I)
    by_name = dict(names)
    for w in writings:
        text = f"{w['label']} {w['text']} {w['detail'].get('evidence', '')}"
        for match in {m.group(1).lower() for m in pattern.finditer(text)}:
            g.link(w["id"], by_name[match], "mentions")
        for ref in _REF.findall(text):
            g.link(w["id"], f"proposal:{ref}", "mentions")


def _first_words(text: str, words: int = 9) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


# --- reading the brain ------------------------------------------------------------------------------------------
def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w{3,}", text.lower())][:12]


def search(graph: Graph, query: str, *, limit: int = 15, kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """The nodes that best match the words, each with what it is linked to (one step)."""
    words = _words(query)
    if not words:
        return []
    degree: dict[str, int] = {}
    for s, t, _k in graph.links:
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    scored: list[tuple[float, dict[str, Any]]] = []
    for n in graph.nodes.values():
        if kinds and n["kind"] not in kinds:
            continue
        label, body = n["label"].lower(), f"{n['text']} {' '.join(str(v) for v in n['detail'].values())}".lower()
        score = sum(3 for w in words if w in label) + sum(1 for w in words if w in body)
        if score:
            scored.append((score + min(degree.get(n["id"], 0), 20) / 40, n))
    scored.sort(key=lambda x: -x[0])
    return [with_neighbours(graph, n["id"]) for _s, n in scored[:limit]]


def with_neighbours(graph: Graph, node_id: str, limit: int = 25) -> dict[str, Any]:
    n = graph.nodes[node_id]
    links = []
    for (s, t, kind) in graph.links:
        if s == node_id:
            other, direction = t, "→"
        elif t == node_id:
            other, direction = s, "←"
        else:
            continue
        o = graph.nodes[other]
        links.append({"id": other, "kind": o["kind"], "label": o["label"], "link": kind, "direction": direction})
    return {"id": n["id"], "kind": n["kind"], "label": n["label"], "text": n["text"], "at": n["at"], "detail": n["detail"], "linked": links[:limit],
            "linked_total": len(links)}
