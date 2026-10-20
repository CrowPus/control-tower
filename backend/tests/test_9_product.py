"""Product and quality: the checks open the owner's pages read-only, keep their history and wake the Engineer on Duty
only after the owner's failed runs in a row;
the Engineer sees failed tasks, a late scheduler and undelivered e-mails, and keeps incidents (never twice for the
same thing) that ring the owner's bell; the Product Analyst's ideas are measured before and after."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import transaction
from app.models import (
    AgentTask,
    EmailDelivery,
    Incident,
    ProductIdea,
    QaRun,
    ScheduledJob,
    TeamApproval,
    TeamEvent,
)
from app.services import platform
from app.services.product import ideas
from app.services.qa import checks, incidents, ops
from app.services.qa import tasks as qa_tasks
from app.services.team import approvals, registry
from app.services.team.approvals import ProposalRefused
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    with _db() as db:
        for model in (QaRun, Incident, ProductIdea, TeamApproval, TeamEvent):
            db.execute(delete(model))
        platform.put(db, "qa", {})
    yield
    with _db() as db:
        platform.put(db, "qa", {})


def _sites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings(), "admin_public_base_url", "https://tower.example")
    with _db() as db:
        checks.save_pages(db, [checks.QaPage(name="Website", url="https://site.example/", contains="<html"),
                               checks.QaPage(name="Terms of use", url="https://site.example/terms", contains="Terms of use"),
                               checks.QaPage(name="Shop", url="https://shop.example/")])


def _handler(seen: list[str], broken: set[str] | None = None) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}{request.url.path}")
        path = request.url.path
        if path in (broken or set()):
            return httpx.Response(502, text="bad gateway")
        if path == "/admin-api/health":
            return httpx.Response(200, json={"status": "ok"})
        titles = {"/terms": "Terms of use", "/privacy": "Privacy policy", "/refunds": "Refund policy", "/contact": "Contact"}
        if path in titles:
            return httpx.Response(200, text=f"<html><title>{titles[path]} | Example</title></html>")
        return httpx.Response(200, text="<html>ok</html>" if not path.startswith("/api/") else '{"ok": true}')
    return handle


def test_the_checks_open_the_owners_pages_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _sites(monkeypatch)
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler(seen)))
    with _db() as db:
        results = checks.run(db, client=client)
    by = {r["name"]: r for r in results}
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]
    assert set(by) == {"Admin", "Website", "Terms of use", "Shop"}
    assert all(s.startswith("GET") for s in seen), "nothing but reading"
    with pytest.raises(ValueError):
        checks.QaPage(name="x", url="ftp://nope")


def test_certificates_and_headers_are_checked_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _sites(monkeypatch)
    monkeypatch.setattr(settings(), "execution_mode", "production")
    seen: list[str] = []
    with _db() as db:
        checks.save_rules(db, {"tls_warn_days": 14})
        results = checks.run(db, client=httpx.Client(transport=httpx.MockTransport(_handler(seen))),
                             cert_probe=lambda host: 7 if host == "site.example" else 80)
    by = {r["name"]: r for r in results}
    assert by["Certificate: site.example"]["ok"] is False and "7 days" in by["Certificate: site.example"]["detail"]
    assert by["Certificate: shop.example"]["ok"] is True and by["Certificate: tower.example"]["ok"] is True
    assert by["Security headers: Website"]["ok"] is False and "strict-transport-security" in by["Security headers: Website"]["detail"]


def test_the_engineer_is_woken_only_after_the_owners_failed_runs_in_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _sites(monkeypatch)
    state = {"broken": {"/terms"}}
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return _handler(seen, state["broken"])(request)

    monkeypatch.setattr(checks, "run", lambda session, client=None, cert_probe=None, _r=checks.run: _r(session, client=httpx.Client(transport=httpx.MockTransport(handle))))
    with _db() as db:
        checks.save_rules(db, {"fail_after": 2, "every_minutes": 10})
        assert checks.due(db)
    first = qa_tasks.sweep("schedule")
    assert first["failed"] == 1 and first["broken"] == []
    with _db() as db:
        assert not checks.due(db), "the owner's interval has not passed"
    second = qa_tasks.sweep("schedule")
    assert second["broken"] == ["Terms of use"]
    third = qa_tasks.sweep("schedule")
    assert third["broken"] == [], "told once, not every run"
    state["broken"] = set()
    fourth = qa_tasks.sweep("schedule")
    assert fourth["recovered"] == ["Terms of use"]
    with _db() as db:
        types = [e.type for e in db.execute(select(TeamEvent).order_by(TeamEvent.id)).scalars()]
        assert types == ["qa.failed", "qa.recovered"]
        history = checks.history(db)
        assert history["failing_now"] == [] and any(f["name"] == "Terms of use" for f in history["flaky"])
    assert "qa.failed" in registry.AGENTS["engineer_on_duty"].subscribes and "alert.opened" in registry.AGENTS["engineer_on_duty"].subscribes


def test_the_engineer_sees_failed_tasks_a_late_scheduler_and_undelivered_mail() -> None:
    now = datetime.now(UTC)
    with _db() as db:
        db.execute(delete(AgentTask).where(AgentTask.task_type == "test.dying"))
        for _ in range(3):
            db.add(AgentTask(queue="maintenance", task_type="test.dying", payload={"secret": "never shown"}, status="dead", run_at=now, finished_at=now,
                             last_error="ProgrammingError: permission denied for table employer_users", created_at=now, updated_at=now))
        db.execute(delete(ScheduledJob).where(ScheduledJob.name == "test_late"))
        db.add(ScheduledJob(name="test_late", task_type="test.late", queue="maintenance", interval_seconds=60, enabled=True, next_run_at=now - timedelta(hours=1),
                            created_at=now, updated_at=now))
        db.add(EmailDelivery(kind="employer_confirm", owner="", recipient="x@example.com", status="failed", last_error="SMTPAuthenticationError", created_at=now, updated_at=now))
        db.flush()
        view = ops.signals(db)
        dying = next(t for t in view["tasks"]["failed"] if t["task"] == "test.dying")
        assert dying["count"] == 3 and "permission denied" in dying["last_error"] and "never shown" not in str(view)
        assert any(j["job"] == "test_late" and j["late_minutes"] >= 59 for j in view["scheduler_late_or_failing"])
        assert any(m["kind"] == "employer_confirm" for m in view["deliveries"]["emails_not_delivered"])
        assert "open_errors" in view and "storage" in view


def test_an_incident_is_kept_once_and_rings_the_bell(admin: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from app.services import bells

    mailed: list[str] = []
    monkeypatch.setattr(bells, "alert_admins", lambda what, path: mailed.append(what))
    with _db() as db:
        row, new = incidents.open_or_note(db, title="Team dispatcher dies every minute", severity="major", key="task:team.dispatch",
                                          impact="No approved action is carried out", cause="permission denied for employer_users", by="engineer_on_duty")
        again, new2 = incidents.open_or_note(db, title="Dispatcher again", severity="major", key="task:team.dispatch", note="Still failing at 15:40", by="engineer_on_duty")
        assert new and not new2 and again.id == row.id and again.notes[-1]["text"] == "Still failing at 15:40"
        with pytest.raises(ValueError, match="severity"):
            incidents.open_or_note(db, title="x", severity="huge", key="", by="engineer_on_duty")
        incident_id = row.id
    assert mailed == ["Incident: Team dispatcher dies every minute"], "once, not for the note"
    bell = admin.get("/admin-api/notifications").json()
    assert any(i["kind"] == "incident_open" and i["params"]["title"].startswith("Team dispatcher") for i in bell["items"])
    listed = admin.get("/admin-api/product/incidents").json()
    assert listed["open"][0]["id"] == incident_id
    after = admin.post(f"/admin-api/product/incidents/{incident_id}", json={"note": "Fixed in 1136c55", "status": "resolved"}).json()
    assert after["open"] == [] and after["recently_resolved"][0]["notes"][-1]["text"] == "Fixed in 1136c55"


def test_an_alert_wakes_the_engineer() -> None:
    from app.models import InsightAlert
    from app.services.insights import alerts

    rule = f"test.{uuid4().hex[:6]}"
    with _db() as db:
        alerts.sync(db, [alerts.Finding(rule=rule, subject="Member app slow", severity="warning", detail={"p95_ms": 3200})])
        event = db.execute(select(TeamEvent).where(TeamEvent.type == "alert.opened")).scalars().first()
        assert event is not None and event.payload["subject"] == "Member app slow" and event.payload["detail"] == {"p95_ms": 3200}
        db.execute(delete(InsightAlert).where(InsightAlert.rule == rule))


def test_an_idea_is_measured_before_and_after(admin: Any) -> None:  # noqa: F811
    assert registry.ACTIONS["product.idea"].ceiling == "L2" and "product.idea" in registry.AGENTS["product_analyst"].actions
    good = {"title": "Show the pricing page right after sign-up", "problem": "Half of new customers never see the prices", "idea": "A step 'Prices' at the end of sign-up",
            "evidence": ["12 of 20 never opened /pricing"], "metric": "agent_runs", "expect": "up", "measure_days": 14}
    with _db() as db:
        with pytest.raises(ProposalRefused, match="metric"):
            approvals.propose(db, agent="product_analyst", action_type="product.idea", title="x", payload={**good, "metric": "happiness"})
        row = approvals.propose(db, agent="product_analyst", action_type="product.idea", title=good["title"], payload=good)
        approvals.decide(db, [row], "approve", admin_id=1)
        approvals.execute_due(db)
        assert row.status == "executed", row.result
        idea_id = row.result["idea_id"]
        with pytest.raises(ValueError, match="shipped"):
            ideas.measure(db, idea_id, "better", "too early")
    listed = admin.post(f"/admin-api/product/ideas/{idea_id}/shipped").json()
    assert next(i for i in listed if i["id"] == idea_id)["status"] == "shipped"
    with _db() as db:
        with pytest.raises(ValueError, match="measuring days"):
            ideas.measure(db, idea_id, "better", "too early")
        db.execute(update(ProductIdea).where(ProductIdea.id == idea_id).values(shipped_at=datetime.now(UTC) - timedelta(days=15)))
        assert ideas.view(db, db.get(ProductIdea, idea_id))["ready_to_measure"] is True  # type: ignore[arg-type]
        done = ideas.measure(db, idea_id, "too_little_data", "Two customers only; read again next month.")
        assert done.status == "measured" and done.verdict == "too_little_data"
    assert admin.post(f"/admin-api/product/ideas/{idea_id}/dropped").status_code == 422, "closed"


def test_the_owner_runs_the_checks_and_sets_rules_and_pages(admin: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _sites(monkeypatch)
    seen: list[str] = []
    real = checks.run
    monkeypatch.setattr(checks, "run", lambda session, client=None, cert_probe=None: real(session, client=httpx.Client(transport=httpx.MockTransport(_handler(seen)))))
    empty = admin.get("/admin-api/product/checks").json()
    assert empty["latest"] is None and [p["name"] for p in empty["pages"]] == ["Website", "Terms of use", "Shop"]
    ran = admin.post("/admin-api/product/checks/run").json()
    assert ran["latest"]["trigger"] == "owner" and ran["latest"]["failed"] == 0 and ran["checks"]
    saved = admin.put("/admin-api/product/checks/settings", json={"rules": {"every_minutes": 30, "fail_after": 2, "slow_ms": None, "tls_warn_days": 14},
                                                                  "pages": [{"name": "Shop", "url": "https://shop.example/", "contains": "Buy"}]})
    body = saved.json()
    assert body["rules"]["every_minutes"] == 30 and body["pages"] == [{"name": "Shop", "url": "https://shop.example/", "contains": "Buy"}]
    assert admin.put("/admin-api/product/checks/settings", json={"rules": {"every_minutes": 1}}).status_code == 422
    assert admin.put("/admin-api/product/checks/settings", json={"rules": {}, "pages": [{"name": "x", "url": "javascript:alert(1)"}]}).status_code == 422
