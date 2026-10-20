"""Outside agents: agents from other providers work like the team — a desk, a credential the owner issues (shown once),
only the tools the owner ticked and never a forbidden one, every change a proposal waiting for clearance, every call
recorded; revoked, rotated, expired, paused and over-limit credentials stop; connectors sign in with OAuth and PKCE;
hosted agents run on the install's server with the owner's provider key."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import transaction
from app.main import app as main_app
from app.models import (
    OutsideAgent,
    OutsideAgentCall,
    OutsideAgentMessage,
    OutsideOAuthClient,
    OutsideOAuthGrant,
    PlatformSetting,
    TeamApproval,
    TeamPlaybook,
    TeamRun,
)
from app.services.outside import core, hosted
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


def _clear() -> None:
    with _db() as db:
        db.execute(delete(TeamApproval).where(TeamApproval.agent.like("ext_%")))
        db.execute(delete(TeamRun).where(TeamRun.agent.like("ext_%")))
        db.execute(delete(TeamPlaybook).where(TeamPlaybook.agent.like("ext_%")))
        for model in (OutsideOAuthGrant, OutsideOAuthClient, OutsideAgentMessage, OutsideAgentCall, OutsideAgent):
            db.execute(delete(model))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == core.KEY))


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    _clear()
    yield
    _clear()


@pytest.fixture()
def gw() -> Iterator[TestClient]:
    with TestClient(main_app) as c:
        yield c


AGENT = {"name": "Claude Analyst", "job": "Reads the numbers and proposes one product change at a time.", "department": "Product",
         "kind": "connected", "client_label": "Claude Code", "tools": ["read_insights", "incidents", "decide_approvals"], "actions": ["product.idea"], "desk": 1}
IDEA = {"title": "Shorter sign-up", "problem": "Half stop at step two", "idea": "One step instead of three", "evidence": ["12 of 24 stop"], "metric": "agent_runs",
        "expect": "up", "measure_days": 14}


def _hire(owner: Any, **over: Any) -> dict[str, Any]:
    r = owner.post("/admin-api/outside", json={**AGENT, **over})
    assert r.status_code == 201, r.text
    return r.json()


def _rpc(gw: TestClient, token: str, method: str, params: dict[str, Any] | None = None, ip: str = "203.0.113.5") -> Any:
    return gw.post("/agent-gateway/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                   headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": ip})


def test_the_owner_hires_only_offered_tools_and_at_most_the_owners_number(admin: Any) -> None:  # noqa: F811
    assert admin.post("/admin-api/outside", json=AGENT).status_code == 422, "decide_approvals is never offered"
    hired = _hire(admin, tools=["read_insights", "incidents"])
    assert hired["key"] == "ext_claude_analyst" and hired["status"] == "draft" and hired["desk"] == 1
    assert admin.post("/admin-api/outside", json={**AGENT, "name": "Second", "tools": [], "desk": 1}).status_code == 422, "desk 1 is taken"
    over = admin.get("/admin-api/outside").json()
    assert "decide_approvals" not in [t["key"] for t in over["tools"]] and "admin.change" not in [a["key"] for a in over["actions"]]
    assert admin.put("/admin-api/outside/rules", json={"max_agents": 1, "paused_all": False, "credential_days": 90, "calls_per_day": 500}).status_code == 200
    assert admin.post("/admin-api/outside", json={**AGENT, "name": "Third", "tools": [], "desk": 2}).status_code == 422, "the owner's limit"
    assert admin.put("/admin-api/outside/rules", json={"max_agents": None, "paused_all": False, "credential_days": 90, "calls_per_day": 500}).status_code == 200
    for n in range(12):
        r = admin.post("/admin-api/outside", json={**AGENT, "name": f"Helper {n}", "tools": [], "desk": None})
        assert r.status_code == 201, r.text


def test_a_connected_agent_works_through_the_gateway_with_its_tools_and_proposes_into_approvals(admin: Any, gw: TestClient) -> None:  # noqa: F811
    hired = _hire(admin, tools=["read_insights", "incidents", "read_web_page"])
    issued = admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).json()
    token = issued["credential"]
    assert token.startswith("ct_agent_") and token in issued["setup"]["claude_code"] and issued["agent"]["status"] == "waiting"
    shown = json.dumps(admin.get(f"/admin-api/outside/{hired['id']}").json())
    assert token not in shown and token[:18] in shown, "only its first characters are shown again, to tell credentials apart"
    init = _rpc(gw, token, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code"}}).json()
    assert init["result"]["serverInfo"]["name"] == "control-tower" and init["result"]["protocolVersion"] == "2025-06-18"
    names = [t["name"] for t in _rpc(gw, token, "tools/list").json()["result"]["tools"]]
    assert {"my_job", "my_directives", "my_messages", "file_report", "read_insights", "incidents", "propose"} <= set(names)
    assert not {"decide_approvals", "propose_admin_change", "read_memories", "ask_agent"} & set(names)
    schema = next(t for t in _rpc(gw, token, "tools/list").json()["result"]["tools"] if t["name"] == "read_web_page")["inputSchema"]
    assert schema["type"] == "object" and schema["properties"]["url"]["type"] == "string"
    ok = _rpc(gw, token, "tools/call", {"name": "read_insights", "arguments": {"days": 7}}).json()["result"]
    assert not ok["isError"] and "kpis" in json.loads(ok["content"][0]["text"])
    refused = _rpc(gw, token, "tools/call", {"name": "decide_approvals", "arguments": {"ids": [1], "decision": "approve"}}).json()["result"]
    assert refused["isError"] and "do not have" in refused["content"][0]["text"]
    no_playbook = _rpc(gw, token, "tools/call", {"name": "propose", "arguments": {"action_type": "playbook.change", "title": "x", "payload_json": "{}"}}).json()["result"]
    assert no_playbook["isError"]
    proposed = _rpc(gw, token, "tools/call", {"name": "propose", "arguments": {"action_type": "product.idea", "title": "Shorter sign-up", "payload_json": json.dumps(IDEA),
                                                                               "reason": "Half stop at step two."}}).json()["result"]
    assert not proposed["isError"], proposed
    with _db() as db:
        card = db.execute(select(TeamApproval).where(TeamApproval.agent == "ext_claude_analyst")).scalar_one()
        assert card.status == "pending" and card.action_type == "product.idea", "it waits for the owner"
        assert db.execute(select(OutsideAgentCall).where(OutsideAgentCall.tool == "decide_approvals")).scalar_one().ok is False, "every call is recorded"
    cards = admin.get("/admin-api/team/approvals").json()["items"]
    assert any(c["agent_name"] == "Claude Analyst" for c in cards), "the owner sees whose it is"
    rest = gw.get("/agent-gateway/tools", headers={"Authorization": f"Bearer {token}"}).json()
    assert rest["agent"] == "Claude Analyst" and any(t["name"] == "incidents" for t in rest["tools"])
    assert admin.get(f"/admin-api/outside/{hired['id']}").json()["agent"]["status"] == "active"


def test_revoked_rotated_expired_paused_wrong_address_and_over_limit_credentials_stop(admin: Any, gw: TestClient) -> None:  # noqa: F811
    hired = _hire(admin, tools=["incidents"], ip_allowlist=["203.0.113.0/24"], calls_per_day=3)
    first = admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).json()["credential"]
    assert _rpc(gw, first, "ping").status_code == 200
    assert _rpc(gw, first, "ping", ip="198.51.100.9").status_code == 403, "only from the owner's addresses"
    second = admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).json()["credential"]
    r = _rpc(gw, first, "ping")
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"], "rotated: the old one stops"
    for _ in range(3):
        _rpc(gw, second, "tools/call", {"name": "incidents", "arguments": {}})
    over = _rpc(gw, second, "tools/call", {"name": "incidents", "arguments": {}}).json()["result"]
    assert over["isError"] and "limit" in over["content"][0]["text"]
    assert admin.post(f"/admin-api/outside/{hired['id']}/pause").json()["status"] == "paused"
    assert _rpc(gw, second, "ping").status_code == 403
    admin.post(f"/admin-api/outside/{hired['id']}/resume")
    with _db() as db:
        db.execute(OutsideAgent.__table__.update().values(credential_expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert _rpc(gw, second, "ping").status_code == 401, "expired"
    assert admin.post(f"/admin-api/outside/{hired['id']}/revoke").json()["status"] == "revoked"
    assert _rpc(gw, second, "ping").status_code == 401
    assert gw.post("/agent-gateway/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401


def test_a_connector_signs_in_with_oauth_and_pkce_using_the_agents_credential(admin: Any, gw: TestClient) -> None:  # noqa: F811
    hired = _hire(admin, tools=["incidents"])
    credential = admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).json()["credential"]
    meta = gw.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"] and gw.get("/.well-known/oauth-protected-resource").json()["resource"].endswith("/agent-gateway/mcp")
    reg = gw.post("/agent-gateway/oauth/register", json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}).json()
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "code_challenge": challenge,
         "code_challenge_method": "S256", "state": "abc"}
    page = gw.get("/agent-gateway/oauth/authorize", params=q)
    assert page.status_code == 200 and "Connect Claude to Control Tower" in page.text
    assert gw.post("/agent-gateway/oauth/authorize", data={**q, "credential": "ct_agent_wrong"}).status_code == 401
    done = gw.post("/agent-gateway/oauth/authorize", data={**q, "credential": credential})
    target = done.headers["refresh"].split("url=", 1)[1]
    code = parse_qs(urlsplit(target).query)["code"][0]
    assert parse_qs(urlsplit(target).query)["state"] == ["abc"]
    bad = gw.post("/agent-gateway/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                                      "redirect_uri": q["redirect_uri"], "code_verifier": "wrong"})
    assert bad.status_code == 400
    tokens = gw.post("/agent-gateway/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                                         "redirect_uri": q["redirect_uri"], "code_verifier": verifier}).json()
    assert tokens["access_token"].startswith("ct_oat_")
    assert _rpc(gw, tokens["access_token"], "tools/list").status_code == 200
    again = gw.post("/agent-gateway/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                                        "redirect_uri": q["redirect_uri"], "code_verifier": verifier})
    assert again.status_code == 400, "a code works once"
    fresh = gw.post("/agent-gateway/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": reg["client_id"]}).json()
    assert fresh["access_token"] != tokens["access_token"]
    admin.post(f"/admin-api/outside/{hired['id']}/revoke")
    assert _rpc(gw, fresh["access_token"], "ping").status_code == 401, "revoking ends connector sign-ins too"


def test_messages_directives_reports_and_the_playbook_reach_the_agent(admin: Any, gw: TestClient) -> None:  # noqa: F811
    hired = _hire(admin, tools=["incidents"])
    token = admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).json()["credential"]
    admin.post(f"/admin-api/outside/{hired['id']}/messages", json={"text": "Focus on sign-ups this week."})
    admin.put(f"/admin-api/outside/{hired['id']}/playbook", json={"text": "One idea at a time, with its number.", "note": "start"})

    def call(name: str, args: dict[str, Any] | None = None) -> Any:
        return json.loads(_rpc(gw, token, "tools/call", {"name": name, "arguments": args or {}}).json()["result"]["content"][0]["text"])

    assert call("my_messages")["messages"][0]["text"] == "Focus on sign-ups this week." and call("my_messages")["messages"] == []
    job = call("my_job")
    assert job["playbook"] == "One idea at a time, with its number." and job["may_propose"][0]["type"] == "product.idea"
    assert "Control Tower" in job["house_rules"] and "Epliv" not in json.dumps(job)
    assert call("my_directives") == {"directives": []}
    assert call("file_report", {"summary": "Read three weeks of numbers; proposed one idea."})["filed"]
    detail = admin.get(f"/admin-api/outside/{hired['id']}").json()
    assert detail["runs"][0]["summary"].startswith("Read three") and detail["messages"][0]["read_at"]


def test_a_hosted_agent_runs_with_the_owners_provider_key(admin: Any) -> None:  # noqa: F811
    hired = _hire(admin, name="Claude Reader", kind="hosted", provider="anthropic", model="claude-sonnet-5-5", provider_key="sk-ant-test-key",
                  tools=["incidents"], actions=[], desk=3, schedule={"times": ["07:00"]})
    assert hired["status"] == "active" and hired["has_provider_key"]
    assert admin.post(f"/admin-api/outside/{hired['id']}/credential", json={}).status_code == 422, "hosted ones need no credential"
    seen: list[dict[str, Any]] = []

    def fake(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"key": request.headers.get("x-api-key"), "tools": [t["name"] for t in body["tools"]]})
        if len(seen) == 1:
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t1", "name": "incidents", "input": {}}],
                                             "usage": {"input_tokens": 100, "output_tokens": 20}})
        assert body["messages"][-1]["content"][0]["type"] == "tool_result"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Checked the incidents: none open."}], "usage": {"input_tokens": 150, "output_tokens": 30}})

    out = hosted.run(hired["id"], trigger="manual", client=httpx.Client(transport=httpx.MockTransport(fake)))
    assert out["status"] == "done" and out["calls"] == 1 and out["tokens"] == 300
    assert seen[0]["key"] == "sk-ant-test-key" and "incidents" in seen[0]["tools"] and "propose" not in seen[0]["tools"]
    with _db() as db:
        run = db.execute(select(TeamRun).where(TeamRun.agent == "ext_claude_reader").order_by(TeamRun.id.desc())).scalars().first()
        assert run is not None and run.status == "done" and "incidents" in run.summary
        stored = db.execute(select(OutsideAgent).where(OutsideAgent.key == "ext_claude_reader")).scalar_one()
        assert "sk-ant-test-key" not in stored.provider_key_ciphertext, "kept encrypted"
    assert "sk-ant" not in json.dumps(admin.get(f"/admin-api/outside/{hired['id']}").json())


def test_nia_can_never_issue_or_change_an_outside_agents_access() -> None:
    from app.services.team import admin_changes

    with pytest.raises(admin_changes.Refused):
        admin_changes.check({"method": "POST", "path": "/admin-api/outside/1/credential", "body": {}})


def test_outside_agents_sit_in_the_office_and_nia_sees_them_and_leaves_them_questions(admin: Any) -> None:  # noqa: F811
    from app.services.office import state as office
    from app.services.team import registry, tools

    hired = _hire(admin, tools=["incidents"], desk=4)
    with _db() as db:
        seat = next(a for a in office.state(db)["agents"] if a["key"] == hired["key"])
    assert seat["department"] == "Outside" and seat["desk"] == 4 and seat["outside"] == "Claude Code" and seat["mode"] == "waiting"
    ctx = tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_db, granted=("team_overview", "ask_agent"), admin_id=1)
    overview = tools.run(ctx, "team_overview", {})
    assert overview["outside_agents"][0]["name"] == "Claude Analyst"
    asked = tools.run(ctx, "ask_agent", {"agent": hired["key"], "question": "Which numbers did you read?"})
    assert asked["left_message"]
    messages = admin.get(f"/admin-api/outside/{hired['id']}").json()["messages"]
    assert messages[0]["sender"] == "nia" and "Which numbers" in messages[0]["text"]


def test_the_long_prompt_is_the_outside_agents_playbook(admin: Any) -> None:  # noqa: F811
    long_prompt = "Rules for the work. " * 400  # about 8,000 characters: too long for the job, fine for the playbook
    hired = _hire(admin, tools=["incidents"], playbook=long_prompt)
    assert admin.get(f"/admin-api/outside/{hired['id']}").json()["playbook"].startswith("Rules for the work.")
    assert admin.post("/admin-api/outside", json={**AGENT, "name": "Too long", "job": "x" * 4001, "tools": [], "desk": 5}).status_code == 422
