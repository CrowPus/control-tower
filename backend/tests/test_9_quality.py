"""The QA checks and the trust watch: the checks only read the owner's pages and report what fails; the trust watch
calls out proposals waiting for clearance longer than the owner's rule and the data-deletion runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete

from app.config import settings
from app.db import transaction
from app.models import TeamApproval
from app.services import platform
from app.services.qa import checks
from app.services.team import registry
from app.services.trust import rules as trust_rules
from app.services.trust import watch


def _db() -> Any:
    return transaction(write=True)


def test_the_checks_only_read_the_owners_pages_and_report_what_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings(), "admin_public_base_url", "")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.url.host == "app.example":
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, text="<html>Welcome to the shop</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with _db() as db:
        platform.put(db, "qa", {})
        assert checks.run(db, client=client) == [], "nothing listed: nothing checked"
        checks.save_pages(db, [checks.QaPage(name="Shop", url="https://shop.example/", contains="Welcome"),
                               checks.QaPage(name="App", url="https://app.example/"),
                               checks.QaPage(name="Wording", url="https://shop.example/about", contains="Not on the page")])
        results = {r["name"]: r for r in checks.run(db, client=client)}
        platform.put(db, "qa", {})
    assert results["Shop"]["ok"] and not results["App"]["ok"] and results["App"]["detail"] == "HTTP 502"
    assert not results["Wording"]["ok"] and "missing" in results["Wording"]["detail"]
    assert all(s.startswith("GET ") for s in seen), "the checks only read"


def test_the_trust_watch_calls_out_proposals_waiting_too_long() -> None:
    now = datetime.now(UTC)
    with _db() as db:
        db.execute(delete(TeamApproval))
        db.add(TeamApproval(agent="nia", action_type="admin.change", level="L3", title="Old card", payload={}, status="pending", created_at=now - timedelta(days=9)))
        db.add(TeamApproval(agent="nia", action_type="admin.change", level="L3", title="New card", payload={}, status="pending", created_at=now))
    with _db() as db:
        assert watch.overdue(db) == [], "no rule: nothing called out"
        trust_rules.put(db, {"wait_alert_days": 7})
        late = watch.overdue(db)
        assert [o["name"] for o in late] == ["Old card"] and late[0]["days"] >= 9
        assert "deletion_runs" in watch.overview(db)["deadlines"]
        platform.put(db, "trust", {})
        db.execute(delete(TeamApproval))
    assert "qa_runner" in registry.AGENTS and registry.AGENTS["qa_runner"].emits == ("qa.failed",)
