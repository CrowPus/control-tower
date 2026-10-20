"""Admin → Trust: what Trust & Compliance watches (the legal deadlines, what waits too long) and the owner's rules for
it, plus an agent's note on a thing that waits for the owner. Changes to rules are in the admin log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.team import reviews
from ..services.trust import rules as trust_rules
from ..services.trust import watch
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api", tags=["admin-oversight"])


class Finding(BaseModel):
    check: str
    ok: bool
    detail: str = ""
    source: str = ""


class ReviewNote(BaseModel):
    id: int
    kind: str
    subject_id: int
    agent: str
    recommendation: str
    summary: str
    findings: list[Finding]
    written_at: str
    outdated: bool


@router.get("/notes/{kind}/{subject_id}", response_model=ReviewNote | None)
def agent_note(kind: str, subject_id: int, _ctx: CurrentAdmin, session: AdminDb) -> ReviewNote | None:
    """The agent's note on one thing that waits for the owner (shown on the page where the owner decides)."""
    note = reviews.of(session, kind, subject_id)
    return ReviewNote.model_validate(note) if note else None


# --- trust ------------------------------------------------------------------------------------------------------
class TrustRules(BaseModel):
    wait_alert_days: int | None = Field(default=None, ge=1, le=90)
    report_alert_days: int | None = Field(default=None, ge=1, le=90)


class TrustDeadlines(BaseModel):
    deletion_runs: list[dict[str, Any]]
    # Members' reports about opportunities still open.
    open_reports: int = 0


class TrustOut(BaseModel):
    deadlines: TrustDeadlines
    overdue: list[dict[str, Any]]
    rules: TrustRules


@router.get("/trust", response_model=TrustOut)
def trust_overview(_ctx: CurrentAdmin, session: AdminDb) -> TrustOut:
    return TrustOut.model_validate(watch.overview(session))


@router.put("/trust/rules", response_model=TrustRules)
def put_trust_rules(data: TrustRules, ctx: OwnerAdmin, session: AdminDb) -> TrustRules:
    before = trust_rules.get(session).__dict__
    after = trust_rules.put(session, data.model_dump())
    record(session, ctx.admin, "trust.rules_changed", target_type="team", ip=ctx.ip, metadata={"before": before, "after": after.__dict__})
    return TrustRules(**after.__dict__)
