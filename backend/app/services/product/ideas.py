"""The Product Analyst's ideas: a change to the product, found in the numbers, with the
Insights number it should move. The owner approves it (product.idea); its baseline is measured at once. Once it is
live the owner marks it shipped; after its measuring days the analyst reads the same number for the days after
shipping and writes down what it did (better, worse, no change, too little data) — the analyst judges, the code
counts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import local_day
from ...models import ProductIdea, TeamApproval
from ..insights import queries
from ..team.approvals import CHECKS, executor

VERDICTS = ("better", "worse", "no_change", "too_little_data")


def _value(session: Session, metric: str, days: int, end: date | None = None) -> float | None:
    spec = queries.BY_KEY.get(metric)
    return queries.kpi(session, spec, days, end)["value"] if spec else None


def view(session: Session, row: ProductIdea, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    out: dict[str, Any] = {"id": row.id, "title": row.title, "problem": row.problem, "idea": row.idea, "evidence": row.evidence, "metric": row.metric,
                           "metric_label": queries.BY_KEY[row.metric].label if row.metric in queries.BY_KEY else row.metric, "expect": row.expect,
                           "measure_days": row.measure_days, "baseline": row.baseline, "status": row.status,
                           "shipped_at": row.shipped_at.date().isoformat() if row.shipped_at else None, "result": row.result, "verdict": row.verdict,
                           "result_note": row.result_note, "measured_at": row.measured_at.date().isoformat() if row.measured_at else None,
                           "created_at": row.created_at.date().isoformat()}
    if row.status == "shipped" and row.shipped_at:
        ready = local_day(row.shipped_at) + timedelta(days=row.measure_days)
        out["ready_to_measure"] = local_day(now) >= ready
        out["measure_from"] = ready.isoformat()
        if out["ready_to_measure"]:
            out["after"] = _value(session, row.metric, row.measure_days, ready)
    return out


def listing(session: Session) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [view(session, r, now) for r in session.execute(select(ProductIdea).order_by(ProductIdea.id.desc()).limit(100)).scalars()]


def measure(session: Session, idea_id: int, verdict: str, note: str) -> ProductIdea:
    """The analyst writes down what a shipped idea did, once its measuring days are over."""
    row = session.get(ProductIdea, idea_id)
    if row is None:
        raise ValueError("There is no such idea.")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    v = view(session, row)
    if row.status != "shipped" or not v.get("ready_to_measure"):
        raise ValueError("Only a shipped idea whose measuring days are over can be measured" + (f" (from {v['measure_from']})." if v.get("measure_from") else "."))
    now = datetime.now(UTC)
    row.result, row.verdict, row.result_note = v.get("after"), verdict, note.strip()[:2000]
    row.status, row.measured_at, row.updated_at = "measured", now, now
    session.flush()
    return row


def _check(session: Session, payload: dict[str, Any]) -> list[str]:
    problems = []
    if not str(payload.get("title") or "").strip() or not str(payload.get("idea") or "").strip():
        problems.append("An idea needs a title and the change itself (idea).")
    if payload.get("metric") not in queries.BY_KEY:
        problems.append(f"metric must be one of the Insights numbers: {', '.join(queries.BY_KEY)}.")
    if payload.get("expect", "up") not in ("up", "down"):
        problems.append("expect is up or down.")
    days = payload.get("measure_days", 14)
    if not isinstance(days, int) or not 7 <= days <= 90:
        problems.append("measure_days is a whole number from 7 to 90.")
    return problems


CHECKS["product.idea"] = _check


@executor("product.idea")
def _accept(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    problems = _check(session, payload)
    if problems:
        raise ValueError(problems[0])
    now = datetime.now(UTC)
    days = int(payload.get("measure_days") or 14)
    row = ProductIdea(title=str(payload["title"]).strip()[:200], problem=str(payload.get("problem") or "").strip(), idea=str(payload["idea"]).strip(),
                      evidence=[str(e)[:500] for e in (payload.get("evidence") or [])][:10], metric=str(payload["metric"]), expect=str(payload.get("expect") or "up"),
                      measure_days=days, baseline=_value(session, str(payload["metric"]), days), status="accepted", approval_id=approval.id, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return {"idea_id": row.id, "baseline": row.baseline, "metric": row.metric, "days": days}
