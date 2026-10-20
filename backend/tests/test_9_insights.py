"""Insights: counting requests, error reports, the daily snapshot of the agents' work and its
backfill, alerts, housekeeping, the Monday report, and the admin's Insights API. Every insight
row is checked for what it must never hold: a person or content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core import ratelimit
from app.db import transaction
from app.main import app as admin_app
from app.models import (
    ErrorGroup,
    InsightAlert,
    KpiDaily,
    RequestMetric,
    TelemetryEvent,
    UptimeCheck,
)
from app.services import system_mail
from app.services.insights import (
    alerts,
    collector,
    maintenance,
    queries,
    snapshot,
    telemetry,
    weekly,
)
from app.services.insights.collector import COLLECTOR, app_of, local_day


def _db():  # type: ignore[no-untyped-def]
    return transaction(write=True)


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    ratelimit.reset_for_tests()
    COLLECTOR.take()
    yield
    COLLECTOR.take()


# --- counting ------------------------------------------------------------------------------------
def test_requests_are_counted_by_app_and_route_template_never_by_real_path() -> None:
    from fastapi.testclient import TestClient

    with TestClient(admin_app) as c:
        c.get("/admin-api/overview")
        c.get("/admin-api/team/agents/987654")  # a real id in the path
        c.get("/agent-gateway/tools")
    assert COLLECTOR.flush(_db) > 0
    with _db() as db:
        rows = db.scalars(select(RequestMetric).where(RequestMetric.granularity == "minute")).all()
        routes = {(r.app, r.route) for r in rows}
        assert ("admin", "/admin-api/overview") in routes
        assert ("gateway", "/agent-gateway/tools") in routes
        assert not any("987654" in r.route for r in rows), "never a real id"
        hit = next(r for r in rows if r.route == "/admin-api/overview")
        assert hit.count >= 1 and sum(hit.histogram) == hit.count and hit.status_class == "4xx"


def test_where_a_request_belongs() -> None:
    assert app_of("/admin-api/health") is None and app_of("/admin-api/team") == "admin"
    assert app_of("/agent-gateway/mcp") == "gateway" and app_of("/assets/index.js") is None


# --- telemetry -----------------------------------------------------------------------------------
def test_error_reports_are_kept_without_anything_personal() -> None:
    body = telemetry.parse(json.dumps({"app": "admin", "version": "2026.10.20", "events": [
        {"kind": "vital", "name": "LCP", "page": "/team/4711?tab=chat", "value": 2300},
        {"kind": "vital", "name": "NOPE", "page": "/", "value": 1},
        {"kind": "error", "type": "TypeError", "message": "Cannot read 'x' of undefined for anna.schmidt@example.com at https://app.example.com/x?token=abcdefabcdefabcdefabcdefabcdef order 12345",
         "page": "/team/4711", "stack": "TypeError: x\n    at render (https://app.example.com/assets/index-abc.js:12:345)"},
    ]}).encode())
    assert body is not None
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/604.1"
    with _db() as db:
        telemetry.store(db, body, ua)
        telemetry.store(db, body, ua)
    with _db() as db:
        events = db.scalars(select(TelemetryEvent).where(TelemetryEvent.version == "2026.10.20")).all()
        assert {e.name for e in events} == {"LCP", "TypeError"}, "unknown vitals are dropped"
        assert next(e for e in events if e.name == "LCP").page == "/team/:id"
        group = db.scalars(select(ErrorGroup).where(ErrorGroup.app == "admin", ErrorGroup.error_type == "TypeError")).one()
        assert group.count == 2 and "anna" not in group.message and "example.com" not in group.message and "12345" not in group.message
        assert "[email]" in group.message and "[url]" in group.message
    assert telemetry.parse(json.dumps({"app": "member_web", "events": []}).encode()) is None, "only known apps report"


def test_the_same_bug_after_a_rebuild_stays_one_group() -> None:
    one = telemetry.fingerprint("mobile", "Error", "x", "Error\n at f (bundle.js?v=1:10:20)")
    two = telemetry.fingerprint("mobile", "Error", "x", "Error\n at f (bundle.js?v=2:11:99)")
    assert one == two
    assert telemetry.page_template("/support/EPL-AB12-CD?x=1") == "/support/:id"


# --- the daily numbers ---------------------------------------------------------------------------
def _agent_work() -> None:
    from app.models import TeamApproval, TeamRun

    now = datetime.now(UTC)
    with _db() as db:
        db.add(TeamRun(agent="nia", trigger="manual", status="done", started_at=now, finished_at=now, cost_usd=0.02))
        db.add(TeamApproval(agent="nia", action_type="admin.change", level="L3", title="Insights test", payload={}, status="approved", created_at=now, decided_at=now))


def test_the_snapshot_counts_today_and_rebuilds_history() -> None:
    _agent_work()
    today = local_day()
    with _db() as db:
        snapshot.run(db, today)
        values = {(k.metric, k.dimension): k.value for k in db.scalars(select(KpiDaily).where(KpiDaily.day == today))}
    assert values[("agents.runs", "nia:done")] >= 1 and values[("clearance.proposed", "")] >= 1 and values[("clearance.decided", "approved")] >= 1
    assert values[("agents.cost_usd", "nia")] >= 0.02
    with _db() as db:
        db.add(KpiDaily(day=today, metric="plugin.orders", dimension="", value=7))
    with _db() as db:
        snapshot.run(db, today)
        assert db.scalar(select(KpiDaily.value).where(KpiDaily.metric == "plugin.orders", KpiDaily.day == today)) == 7, "a plugin's numbers survive the rerun"
        db.execute(delete(KpiDaily))
        days = snapshot.backfill(db)
        assert days >= 1 and snapshot.days_with_data(db) == days
    with _db() as db:
        item = queries.kpi(db, queries.BY_KEY["agent_runs"], 7)
        assert item["value"] >= 1 and len(item["spark"]) == 7


def test_request_numbers_fold_into_hours_and_days_and_keep_their_percentiles() -> None:
    old = datetime.now(UTC) - timedelta(hours=5)
    with _db() as db:
        for ms in (10, 30, 90, 2500):
            db.add(RequestMetric(granularity="minute", bucket=old.replace(second=0, microsecond=0), app="admin", route="/admin-api/fold-test", status_class="2xx",
                                 count=1, sum_ms=ms, max_ms=ms, histogram=[1 if i == next(j for j, b in enumerate(collector.BUCKETS) if ms <= b) else 0 for i in range(len(collector.BUCKETS))]))
    with _db() as db:
        maintenance.prune(db)
    with _db() as db:
        rows = db.scalars(select(RequestMetric).where(RequestMetric.route == "/admin-api/fold-test")).all()
        assert len(rows) == 1 and rows[0].granularity == "hour" and rows[0].count == 4 and rows[0].max_ms == 2500
        assert snapshot.hist_percentile(rows[0].histogram, 0.5) <= 100 and 1600 < snapshot.hist_percentile(rows[0].histogram, 0.99) <= 3200


# --- alerts --------------------------------------------------------------------------------------
def test_an_address_that_is_down_alerts_once_and_resolves_once() -> None:
    now = datetime.now(UTC)
    with _db() as db:
        for i in range(3):
            db.add(UptimeCheck(checked_at=now - timedelta(seconds=10 * i), target="Test-Site", ok=False, status=502, ms=50))
    before = len([m for m in system_mail.OUTBOX if m.kind == "admin_attention"])
    with _db() as db:
        assert alerts.sync(db, alerts.evaluate(db))["opened"] >= 1
    with _db() as db:
        assert alerts.sync(db, alerts.evaluate(db))["opened"] == 0, "still down: no second e-mail"
    mails = [m for m in system_mail.OUTBOX if m.kind == "admin_attention"][before:]
    assert any("Test-Site" in m.subject for m in mails)
    with _db() as db:
        db.add(UptimeCheck(checked_at=datetime.now(UTC) + timedelta(seconds=1), target="Test-Site", ok=True, status=200, ms=40))
    with _db() as db:
        alerts.sync(db, alerts.evaluate(db))
        row = db.scalars(select(InsightAlert).where(InsightAlert.rule == "uptime:Test-Site")).one()
        assert row.resolved_at is not None
    assert any("Resolved" in m.subject and "Test-Site" in m.subject for m in system_mail.OUTBOX if m.kind == "admin_attention")


# --- weekly report -------------------------------------------------------------------------------
def test_the_monday_report_goes_out_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monday = datetime(2026, 10, 5, 8, 30, tzinfo=UTC)  # 08:30 in the owner's time zone (UTC in tests)
    with _db() as db:
        week_end = weekly.due(db, monday)
        assert week_end == date(2026, 10, 4)
        assert weekly.send(db, week_end)
        assert weekly.due(db, monday) is None, "once a week"
        assert weekly.due(db, datetime(2026, 10, 6, 8, 30, tzinfo=UTC)) is None, "Mondays only"
    mail = next(m for m in reversed(system_mail.OUTBOX) if m.kind == "insights_weekly")
    assert "Agent runs" in mail.body and "2026-09-28 – 2026-10-04" in mail.subject


# --- the admin's Insights ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin() -> Iterator[Any]:
    from contextlib import redirect_stdout
    from io import StringIO
    from urllib.parse import parse_qs, urlparse

    from app.admin_cli import main as admin_cli
    from app.main import app as admin_app
    from tests.test_9_admin_dashboard import AdminClient, _fresh_code

    email = f"insights-owner-{uuid4().hex[:6]}@tower.example"
    buffer = StringIO()
    with redirect_stdout(buffer):
        admin_cli(["create-owner", "--email", email, "--name", "Ida Insights"])
    link = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if "accept-invite?token=" in line)
    with AdminClient(admin_app) as c:
        c.post("/admin-api/auth/accept-invite", json={"token": parse_qs(urlparse(link).query)["token"][0], "password": "insights-owner-passphrase-2026"})
        r = c.post("/admin-api/auth/login", json={"email": email, "password": "insights-owner-passphrase-2026"})
        c.csrf = r.json()["csrf_token"]
        secret = c.post("/admin-api/auth/enroll/start").json()["secret"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(secret, email)}).status_code == 200
        yield c


def test_every_insights_page_answers_for_the_admin(admin: Any) -> None:
    _agent_work()
    with _db() as db:
        snapshot.run(db, local_day())
    today = admin.get("/admin-api/insights/today?days=30").json()
    assert today["north_star"]["key"] == "cleared" and len(today["kpis"]) == 5 and len(today["activity"]["days"]) == 30
    for page in ("agents", "money", "platform"):
        r = admin.get(f"/admin-api/insights/{page}?days=7")
        assert r.status_code == 200, (page, r.text)
        assert all(len(s["values"]) == 7 for s in r.json()["series"]["series"])
    for gone in ("growth", "funnels", "website"):
        assert admin.get(f"/admin-api/insights/{gone}").status_code == 404
    perf = admin.get("/admin-api/insights/performance?hours=24")
    assert perf.status_code == 200 and len(perf.json()["axis"]) == 24 and "phone" not in perf.json()
    assert admin.get("/admin-api/insights/errors").status_code == 200
    assert admin.get("/admin-api/insights/alerts").status_code == 200
    csv = admin.get("/admin-api/insights/export?metric=agents.runs&days=7")
    assert csv.status_code == 200 and csv.text.startswith("day,metric,dimension,value")
    saved = admin.put("/admin-api/insights/settings", json={"alerts": {"p95_ms": 1500, "made_up": 1}}).json()
    assert saved["alerts"]["p95_ms"] == 1500 and "made_up" not in saved["alerts"]


def test_no_insight_table_holds_a_person() -> None:
    from app.models import insights as models

    for model in (models.KpiDaily, models.RequestMetric, models.TelemetryEvent, models.ErrorGroup, models.UptimeCheck, models.InsightAlert):
        columns = set(model.__table__.columns.keys())
        assert not columns & {"user_id", "employer_id", "member_id", "account_id", "email", "ip", "ip_address", "user_agent"}, model
