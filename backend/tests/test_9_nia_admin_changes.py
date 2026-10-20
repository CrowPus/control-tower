"""Nia changes things in the admin (services/team/admin_changes.py): she proposes one exact admin
request, it waits as the owner's decision, the owner's own page sends it with the owner's own
sign-in, and the server records what came back. Never for sign-in, admin accounts,
authenticator recovery or approvals; never approved in bulk, by voice or by chat."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.db import admin_transaction, transaction
from app.models import AdminAuditLog, TeamApproval
from app.services import platform
from app.services.team import admin_changes, approvals, registry, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


def _context() -> tools.Context:
    return tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_db, granted=("propose_admin_change",))


@pytest.fixture(autouse=True)
def clean() -> Any:
    yield
    with _db() as db:
        db.execute(delete(TeamApproval).where(TeamApproval.action_type == admin_changes.ACTION))
        platform.put(db, "trust", {})


def test_only_real_admin_changes_outside_the_owners_own_areas() -> None:
    whole = {"wait_alert_days": 7, "report_alert_days": 14}
    clean = admin_changes.check({"method": "put", "path": "/admin-api/trust/rules", "body": whole})
    assert clean == {"method": "PUT", "path": "/admin-api/trust/rules", "query": {}, "body": whole}
    with pytest.raises(admin_changes.Refused, match="missing report_alert_days"):
        admin_changes.check({"method": "PUT", "path": "/admin-api/trust/rules", "body": {"wait_alert_days": 3}})  # would reset the other rule
    for bad, why in (
        ({"method": "GET", "path": "/admin-api/trust"}, "A change is"),
        ({"method": "PUT", "path": "/admin-api/nothing/here"}, "There is no"),
        ({"method": "PUT", "path": "https://evil.example/admin-api/trust/rules"}, "starts with /admin-api/"),
        ({"method": "PUT", "path": "/admin-api/trust/../admins"}, "starts with /admin-api/"),
        ({"method": "POST", "path": "/admin-api/admins"}, "never Nia's"),
        ({"method": "POST", "path": "/admin-api/auth/logout"}, "never Nia's"),
        ({"method": "POST", "path": "/admin-api/team/approvals/decide"}, "never Nia's"),
        ({"method": "PUT", "path": "/admin-api/trust/rules", "body": "text"}, "body is"),
    ):
        with pytest.raises(admin_changes.Refused, match=why):
            admin_changes.check(bad)


def test_nia_looks_up_what_a_function_takes() -> None:
    found = admin_changes.describe(["/admin-api/trust"])
    settings = next(e for e in found if e["method"] == "PUT" and e["path"] == "/admin-api/trust/rules")
    assert settings["change"] and "wait_alert_days" in json.dumps(settings["body"])
    assert not [e for e in admin_changes.describe([]) if e["path"].startswith(("/admin-api/admins", "/admin-api/auth"))]
    by_section = tools.CATALOGUE["admin_endpoints"][0](None, section="trust")  # type: ignore[arg-type]
    assert {e["path"] for e in by_section["endpoints"]} >= {"/admin-api/trust", "/admin-api/trust/rules"}


def test_nia_proposes_and_the_owners_page_carries_it_out(admin: Any) -> None:  # noqa: F811 - the fixture
    propose = tools.CATALOGUE["propose_admin_change"][0]
    refused = propose(_context(), method="POST", path="/admin-api/admins", title="Make me an owner")
    assert "never Nia's" in refused["error"]
    made = propose(_context(), method="PUT", path="/admin-api/trust/rules", title="Call out what waits a week",
                   body_json=json.dumps({"wait_alert_days": 7, "report_alert_days": 14}), reason="You asked for it.")
    approval_id = made["proposal"]
    with _db() as db:
        row = db.get(TeamApproval, approval_id)
        assert (row.level, row.status, row.agent) == ("L3", "pending", "nia")  # type: ignore[union-attr]

    # Never in bulk (and Nia's own decide tool refuses L3 anyway).
    bulk = admin.post("/admin-api/team/approvals/decide", json={"ids": [approval_id], "decision": "approve", "note": ""})
    assert bulk.status_code in (400, 422) and "one by one" in bulk.text
    by_chat = tools.CATALOGUE["decide_approvals"][0](tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_db, granted=(), admin_id=1),
                                                    ids=[approval_id], decision="approve")
    assert "on screen" in by_chat["error"]

    # The owner's page: check, send with the owner's own sign-in, record.
    request = admin.post(f"/admin-api/team/approvals/{approval_id}/admin-change/check", json={}).json()
    assert request["path"] == "/admin-api/trust/rules"
    edited = {**request, "body": {**request["body"], "wait_alert_days": 5}}
    sent = admin.request(request["method"], request["path"], json=edited["body"])
    assert sent.status_code == 200
    done = admin.post(f"/admin-api/team/approvals/{approval_id}/admin-change/done",
                      json={"payload": edited, "status_code": sent.status_code, "answer": sent.json(), "note": "One is enough."}).json()
    assert done["status"] == "executed" and done["edited_payload"]["body"]["wait_alert_days"] == 5 and done["result"]["status"] == 200
    with _db() as db:
        assert platform.get(db, "trust")["wait_alert_days"] == 5
    # The admin log is read on the admin's connection (the member app's role may not read it).
    with admin_transaction() as db:
        logged = db.execute(select(AdminAuditLog).where(AdminAuditLog.action == "team.admin_change_carried_out", AdminAuditLog.target_id == str(approval_id))).scalar_one()
        assert (logged.metadata_["path"], logged.metadata_["agent"], logged.result) == ("/admin-api/trust/rules", "nia", "ok")
    again = admin.post(f"/admin-api/team/approvals/{approval_id}/admin-change/done", json={"status_code": 200})
    assert again.status_code in (400, 422), "decided once"


def test_a_failed_change_is_recorded_as_failed_and_an_edit_cannot_reach_a_closed_area(admin: Any) -> None:  # noqa: F811 - the fixture
    whole = {"wait_alert_days": 7, "report_alert_days": 14}
    refused = tools.CATALOGUE["propose_admin_change"][0](_context(), method="PUT", path="/admin-api/trust/rules", title="Too many days",
                                                         body_json=json.dumps({**whole, "wait_alert_days": 500}))
    assert "wait_alert_days" in refused["error"], "a body the admin would refuse is never proposed (Nia hears why at once)"
    # One the admin still refuses when it is sent (the owner edited it to a value the form rejects) is recorded as failed.
    made = tools.CATALOGUE["propose_admin_change"][0](_context(), method="PUT", path="/admin-api/trust/rules", title="A week", body_json=json.dumps(whole))
    approval_id = made["proposal"]
    sneaky = admin.post(f"/admin-api/team/approvals/{approval_id}/admin-change/check", json={"payload": {"method": "POST", "path": "/admin-api/admins", "body": {}}})
    assert sneaky.status_code in (400, 422) and "never Nia's" in sneaky.text
    sent = admin.put("/admin-api/trust/rules", json={**whole, "wait_alert_days": 0})
    assert sent.status_code == 422, "the admin's own rules apply to Nia's change"
    done = admin.post(f"/admin-api/team/approvals/{approval_id}/admin-change/done", json={"status_code": sent.status_code, "answer": sent.json()}).json()
    assert done["status"] == "failed" and done["result"]["status"] == 422


def test_the_worker_never_carries_out_an_admin_change() -> None:
    with _db() as db:
        row = approvals.propose(db, agent="nia", action_type=admin_changes.ACTION, title="x", payload={"method": "PUT", "path": "/admin-api/trust/rules", "body": {}})
        row.status = "approved"
        db.flush()
        approvals.execute_due(db)
        assert row.status == "failed" and "on screen" in row.result["error"]
