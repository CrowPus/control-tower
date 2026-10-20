"""The Marketing Lead's campaign plans. The Lead proposes a plan for a period
(marketing.plan): goals with numbers, the audiences (people in the focus countries, for the open opportunities), the
campaigns per channel, and a brief for each specialist (the Graphic Designer's video themes among them). When the owner approves it, it becomes the
active plan (the one before is replaced) and each brief reaches its agent as a `marketing.brief`."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import MarketingPlan, TeamApproval
from ..team import events
from ..team.approvals import executor

# The specialists a brief may go to.
SPECIALISTS = ("content_community", "graphic_designer")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def active(session: Session) -> MarketingPlan | None:
    return session.execute(select(MarketingPlan).where(MarketingPlan.status == "active").order_by(MarketingPlan.id.desc())).scalars().first()


def view(row: MarketingPlan) -> dict[str, Any]:
    return {"id": row.id, "title": row.title, "period_start": row.period_start, "period_end": row.period_end, "summary": row.summary, "status": row.status,
            "goals": row.body.get("goals") or [], "audiences": row.body.get("audiences") or [], "campaigns": row.body.get("campaigns") or [],
            "briefs": row.body.get("briefs") or {}, "measures": row.body.get("measures") or [], "agent": row.agent, "created_at": row.created_at.isoformat()}


@executor("marketing.plan")
def _plan(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
    start, end = str(payload.get("period_start") or ""), str(payload.get("period_end") or "")
    if not DATE.match(start) or not DATE.match(end) or end < start:
        raise ValueError("period_start and period_end are dates (YYYY-MM-DD), the end after the start.")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("The plan needs a title.")
    raw_briefs = payload.get("briefs")
    briefs: dict[str, Any] = raw_briefs if isinstance(raw_briefs, dict) else {}
    unknown = [k for k in briefs if k not in SPECIALISTS]
    if unknown:
        raise ValueError(f"Briefs go to {', '.join(SPECIALISTS)}; not to {', '.join(unknown)}.")
    for row in session.execute(select(MarketingPlan).where(MarketingPlan.status == "active")).scalars():
        row.status = "replaced"
    body: dict[str, Any] = {k: payload[k] for k in ("goals", "audiences", "campaigns", "measures") if isinstance(payload.get(k), list)}
    sent: dict[str, str] = {k: str(v)[:4000] for k, v in briefs.items()}
    body["briefs"] = sent
    plan = MarketingPlan(title=title[:200], period_start=start, period_end=end, summary=str(payload.get("summary") or "")[:4000], body=body, status="active",
                         approval_id=approval.id, agent=approval.agent, created_at=datetime.now(UTC))
    session.add(plan)
    session.flush()
    for agent, brief in sent.items():
        events.emit(session, "marketing.brief", approval.agent, {"plan_id": plan.id, "to": agent, "brief": brief, "period_end": end})
    return {"plan": plan.id, "briefs": list(sent)}
