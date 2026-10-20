"""Admin → Product: the Product Analyst's ideas (the owner marks them shipped or drops
them), the QA Runner's checks (latest run, history, run now, the owner's rules and test accounts) and the Engineer on
Duty's incidents (the owner adds notes or closes them). Changes to rules, accounts, ideas and incidents are in the
admin log; passwords and the authenticator secret are stored encrypted and never shown."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import NotFound, ValidationFailed
from ..models import ProductIdea, QaRun
from ..services.product import ideas
from ..services.qa import checks, incidents
from ..services.qa.checks import QaPage
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api/product", tags=["admin-product"])


# --- checks ---------------------------------------------------------------------------------------------------
class QaRules(BaseModel):
    every_minutes: int | None = Field(default=None, ge=5, le=1440)
    fail_after: int | None = Field(default=None, ge=1, le=10)
    slow_ms: int | None = Field(default=None, ge=100, le=60000)
    tls_warn_days: int | None = Field(default=None, ge=1, le=90)


class QaCheckOut(BaseModel):
    name: str
    url: str
    ok: bool
    status: int
    ms: int
    detail: str = ""
    group: str = "pages"
    slow: bool = False


class QaRunOut(BaseModel):
    id: int
    started_at: datetime
    trigger: str
    total: int
    failed: int
    slowest_ms: int


class QaOut(BaseModel):
    latest: QaRunOut | None
    checks: list[QaCheckOut]
    runs: list[QaRunOut]
    failing_now: list[dict[str, Any]]
    flaky: list[dict[str, Any]]
    rules: QaRules
    # The owner's pages the checks open (name, address, the text the answer must contain).
    pages: list[QaPage]


class QaSettingsIn(BaseModel):
    rules: QaRules
    pages: list[QaPage] = Field(default_factory=list, max_length=checks.MAX_PAGES)


def _qa(session: AdminDb) -> QaOut:
    runs = list(session.execute(select(QaRun).order_by(QaRun.started_at.desc()).limit(30)).scalars())
    history = checks.history(session)
    r = checks.rules(session)

    def out(row: QaRun) -> QaRunOut:
        return QaRunOut(id=row.id, started_at=row.started_at, trigger=row.trigger, total=row.total, failed=row.failed, slowest_ms=row.slowest_ms)

    return QaOut(latest=out(runs[0]) if runs else None, checks=[QaCheckOut.model_validate(c) for c in (runs[0].checks if runs else [])], runs=[out(x) for x in runs],
                 failing_now=history["failing_now"], flaky=history["flaky"], rules=QaRules(**{k: getattr(r, k) for k in checks.RULE_FIELDS}), pages=checks.pages(session))


@router.get("/checks", response_model=QaOut)
def product_checks(_ctx: CurrentAdmin, session: AdminDb) -> QaOut:
    return _qa(session)


@router.post("/checks/run", response_model=QaOut)
def run_checks_now(ctx: CurrentAdmin, session: AdminDb) -> QaOut:
    from ..services.team import events

    row = checks.record(session, checks.run(session), "owner")
    broken, recovered = checks.changes(session)
    if broken:
        events.emit(session, "qa.failed", "qa", {"run_id": row.id, "checks": broken})
    if recovered:
        events.emit(session, "qa.recovered", "qa", {"run_id": row.id, "checks": recovered})
    record(session, ctx.admin, "product.checks_run", target_type="qa", target_id=row.id, ip=ctx.ip, metadata={"failed": row.failed})
    return _qa(session)


@router.put("/checks/settings", response_model=QaOut)
def put_check_settings(data: QaSettingsIn, ctx: OwnerAdmin, session: AdminDb) -> QaOut:
    checks.save_rules(session, data.rules.model_dump())
    checks.save_pages(session, data.pages)
    record(session, ctx.admin, "product.checks_settings_changed", target_type="qa", ip=ctx.ip,
           metadata={"rules": data.rules.model_dump(), "pages": [p.url for p in data.pages]})
    return _qa(session)


# --- incidents ------------------------------------------------------------------------------------------------
class IncidentOut(BaseModel):
    id: int
    title: str
    severity: str
    status: str
    key: str
    impact: str
    cause: str
    fix: str
    notes: list[dict[str, Any]]
    opened_by: str
    started_at: str
    resolved_at: str | None
    updated_at: str


class IncidentsOut(BaseModel):
    open: list[IncidentOut]
    recently_resolved: list[IncidentOut]


class IncidentIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    severity: Literal["critical", "major", "minor"]
    impact: str = Field(default="", max_length=4000)
    note: str = Field(default="", max_length=2000)


class IncidentChangeIn(BaseModel):
    note: str = Field(default="", max_length=2000)
    status: Literal["open", "monitoring", "resolved"] | None = None
    severity: Literal["critical", "major", "minor"] | None = None
    fix: str | None = Field(default=None, max_length=4000)


def _incidents(session: AdminDb) -> IncidentsOut:
    data = incidents.listing(session)
    return IncidentsOut(open=[IncidentOut.model_validate(i) for i in data["open"]], recently_resolved=[IncidentOut.model_validate(i) for i in data["recently_resolved"]])


@router.get("/incidents", response_model=IncidentsOut)
def product_incidents(_ctx: CurrentAdmin, session: AdminDb) -> IncidentsOut:
    return _incidents(session)


@router.post("/incidents", response_model=IncidentsOut, status_code=201)
def add_incident(data: IncidentIn, ctx: CurrentAdmin, session: AdminDb) -> IncidentsOut:
    row, _new = incidents.open_or_note(session, title=data.title, severity=data.severity, key="", impact=data.impact, note=data.note, by=f"admin:{ctx.admin.email}"[:40])
    record(session, ctx.admin, "product.incident_opened", target_type="incident", target_id=row.id, ip=ctx.ip, metadata={"severity": data.severity})
    return _incidents(session)


@router.post("/incidents/{incident_id}", response_model=IncidentsOut)
def change_incident(incident_id: int, data: IncidentChangeIn, ctx: CurrentAdmin, session: AdminDb) -> IncidentsOut:
    try:
        incidents.update(session, incident_id, by=f"admin:{ctx.admin.email}"[:40], note=data.note, status=data.status, severity=data.severity, fix=data.fix)
    except ValueError as exc:
        raise NotFound(str(exc), code="INCIDENT_NOT_FOUND") from exc
    record(session, ctx.admin, "product.incident_changed", target_type="incident", target_id=incident_id, ip=ctx.ip, metadata={"status": data.status, "severity": data.severity})
    return _incidents(session)


# --- ideas ----------------------------------------------------------------------------------------------------
class IdeaOut(BaseModel):
    id: int
    title: str
    problem: str
    idea: str
    evidence: list[str]
    metric: str
    metric_label: str
    expect: str
    measure_days: int
    baseline: float | None
    status: str
    shipped_at: str | None
    result: float | None
    verdict: str
    result_note: str
    measured_at: str | None
    created_at: str
    ready_to_measure: bool | None = None
    measure_from: str | None = None
    after: float | None = None


@router.get("/ideas", response_model=list[IdeaOut])
def product_ideas(_ctx: CurrentAdmin, session: AdminDb) -> list[IdeaOut]:
    return [IdeaOut.model_validate(i) for i in ideas.listing(session)]


@router.post("/ideas/{idea_id}/{step}", response_model=list[IdeaOut])
def idea_step(idea_id: int, step: Literal["shipped", "dropped"], ctx: OwnerAdmin, session: AdminDb) -> list[IdeaOut]:
    """The owner marks an idea as live (its measuring days start) or drops it."""
    row = session.get(ProductIdea, idea_id)
    if row is None:
        raise NotFound("There is no such idea.", code="IDEA_NOT_FOUND")
    if step == "shipped" and row.status != "accepted":
        raise ValidationFailed("Only an accepted idea can be marked as shipped.", code="IDEA_NOT_ACCEPTED")
    if step == "dropped" and row.status in ("measured", "dropped"):
        raise ValidationFailed("This idea is already closed.", code="IDEA_CLOSED")
    now = datetime.now(UTC)
    row.status, row.updated_at = step, now
    if step == "shipped":
        row.shipped_at = now
    record(session, ctx.admin, f"product.idea_{step}", target_type="product_idea", target_id=idea_id, ip=ctx.ip, metadata={"title": row.title})
    return [IdeaOut.model_validate(i) for i in ideas.listing(session)]
