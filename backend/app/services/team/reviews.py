"""The agents' notes on what waits for the owner: an agent pre-checks a thing the owner decides on and writes one note
per thing — a newer check replaces the older one, never a second note. The note stays inside the install (no approval
needed); the owner still decides on the page where the decision is made.

`basis` is the state of the thing the agent read: once the thing changes, the note shows as outdated and the agent
checks it again. A plugin adds the kinds of records its agents review."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import AgentReview
from ...models.oversight import REVIEW_KINDS

RECOMMENDATIONS: dict[str, tuple[str, ...]] = {}
# Which agent writes which notes (an agent's design, not a setting).
WRITERS: dict[str, tuple[str, ...]] = {}
# What "waiting for the owner" means for each kind: only then is a note useful.
WAITING: dict[str, tuple[str, ...]] = {}
MAX_FINDINGS = 20


def _subject(session: Session, kind: str, subject_id: int) -> Any:
    models: dict[str, Any] = {}
    return session.get(models[kind], subject_id) if kind in models else None


def _state(kind: str, row: Any) -> str:
    return str(getattr(row, "state", None) or row.status)


def basis(kind: str, row: Any) -> str:
    """The state of the thing as the agent read it: its status and when it last changed."""
    changed = getattr(row, "updated_at", None) or getattr(row, "reviewed_at", None) or getattr(row, "decided_at", None)
    return f"{_state(kind, row)}|{changed.isoformat() if changed else ''}"[:64]


def save(session: Session, *, agent: str, kind: str, subject_id: int, recommendation: str, summary: str, findings: list[dict[str, Any]] | None,
         run_id: int | None = None) -> tuple[AgentReview, bool]:
    """Write (or replace) the agent's note on one thing. Raises ValueError with what is wrong."""
    if kind not in REVIEW_KINDS:
        raise ValueError(f"kind is one of {', '.join(REVIEW_KINDS)}.")
    if kind not in WRITERS.get(agent, ()):
        raise ValueError(f"Notes on {kind} are not this agent's work.")
    if recommendation not in RECOMMENDATIONS[kind]:
        raise ValueError(f"recommendation for {kind} is one of {', '.join(RECOMMENDATIONS[kind])}.")
    summary = (summary or "").strip()
    if len(summary) < 20:
        raise ValueError("Write the summary: what you checked, what you found and why you recommend it (two to five sentences).")
    row = _subject(session, kind, int(subject_id))
    if row is None:
        raise ValueError(f"There is no {kind} {subject_id}.")
    if _state(kind, row) not in WAITING[kind]:
        raise ValueError(f"This {kind} does not wait for the owner any more (it is {_state(kind, row)}); no note is needed.")
    clean: list[dict[str, Any]] = []
    for f in (findings or [])[:MAX_FINDINGS]:
        if isinstance(f, dict) and str(f.get("check") or "").strip():
            clean.append({"check": str(f["check"])[:80], "ok": bool(f.get("ok")), "detail": str(f.get("detail") or "")[:600],
                          "source": str(f.get("source") or "")[:500]})
    now = datetime.now(UTC)
    note = session.execute(select(AgentReview).where(AgentReview.kind == kind, AgentReview.subject_id == row.id)).scalar_one_or_none()
    new = note is None
    if note is None:
        note = AgentReview(kind=kind, subject_id=row.id, created_at=now)
        session.add(note)
    note.agent, note.recommendation, note.summary, note.findings = agent, recommendation, summary[:4000], clean
    note.basis, note.run_id, note.updated_at = basis(kind, row), run_id, now
    session.flush()
    return note, new


def view(note: AgentReview, current_basis: str) -> dict[str, Any]:
    return {"id": note.id, "kind": note.kind, "subject_id": note.subject_id, "agent": note.agent, "recommendation": note.recommendation,
            "summary": note.summary, "findings": note.findings, "written_at": note.updated_at.isoformat(), "outdated": note.basis != current_basis}


def of(session: Session, kind: str, subject_id: int) -> dict[str, Any] | None:
    note = session.execute(select(AgentReview).where(AgentReview.kind == kind, AgentReview.subject_id == subject_id)).scalar_one_or_none()
    if note is None:
        return None
    row = _subject(session, kind, subject_id)
    return view(note, basis(kind, row) if row is not None else "")


def many(session: Session, kind: str, rows: list[Any]) -> dict[int, dict[str, Any]]:
    """The notes on these things, by id (outdated when the thing changed since)."""
    by_id = {r.id: r for r in rows}
    if not by_id:
        return {}
    notes = session.execute(select(AgentReview).where(AgentReview.kind == kind, AgentReview.subject_id.in_(tuple(by_id)))).scalars()
    return {n.subject_id: view(n, basis(kind, by_id[n.subject_id])) for n in notes}
