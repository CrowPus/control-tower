"""Plugins (docs/PLUGINS.md): a Python plugin (examples/plugins/hello_shop, loaded from PLUGINS_DIR) and a project
connected over HTTP add tools, actions, numbers and templates; they stay off until the owner switches them on; personal
fields never reach an agent; actions run only after clearance, once; everything between Control Tower and a project is
signed; broken plugins are reported and left out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import KpiDaily, OwnerAgent, PlatformSetting, TeamApproval, TeamEvent, TeamRun
from app.plugin_api import Action, Param, Plugin, Tool
from app.services.insights import queries
from app.services.plugins import connector, loader, signing
from app.services.plugins import core as plugins
from app.services.team import agents, approvals, config, registry, runner, templates, tools
from tests.test_9_team import (  # noqa: F401 - the signed-in owner (module fixture)
    Scripted,
    admin,
)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


def _switch(on: bool, **values: str) -> None:
    with _db() as db:
        state = {k: dict(v) for k, v in plugins.state().items()}
        mine = state.setdefault("hello_shop", {})
        mine["enabled"] = on
        mine["values"] = {**(mine.get("values") or {}), **values}
        plugins.save_state(db, state)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with _db() as db:
            for model in (TeamApproval, TeamRun, TeamEvent, OwnerAgent):
                db.execute(delete(model))
            db.execute(delete(KpiDaily).where(KpiDaily.metric.like("plugin.%")))
            db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("plugins", "plugin_connections", "agents"))))
            plugins.save_state(db, {})
            plugins.save_connections(db, {})
        registry.AGENTS.changed()
        featured = plugins.context("hello_shop").data_dir / "featured.json"
        featured.unlink(missing_ok=True)

    plugins.ensure()
    wipe()
    yield
    wipe()
    connector.TRANSPORT = None
    runner.use_model(runner.ProviderModel())


def test_the_example_plugin_loads_and_stays_off_until_the_owner_switches_it_on() -> None:
    plugins.ensure()
    loaded = plugins.LOADED["hello_shop"]
    assert loaded.source == "python" and loaded.warnings == []
    assert loaded.tools == ("hello_shop_products", "hello_shop_orders") and loaded.actions == ("hello_shop.feature",)
    assert "hello_shop_orders" in queries.BY_KEY and "hello_shop.merchandiser" in templates.BY_KEY
    assert registry.ACTIONS["hello_shop.feature"].ceiling == "L2"
    assert "hello_shop_products" not in agents.offered_tools() and "hello_shop.feature" not in agents.offered_actions()
    assert "hello_shop.merchandiser" not in {t.key for t in templates.offered()}
    assert "Hello Shop plugin is switched off" in tools.CATALOGUE["hello_shop_products"][0](None)["error"]
    _switch(True, currency="EUR")
    assert "hello_shop_products" in agents.offered_tools() and "hello_shop.feature" in agents.offered_actions()
    assert "hello_shop.merchandiser" in {t.key for t in templates.offered()}
    assert tools.CATALOGUE["hello_shop_products"][0](None, low_stock_below=5)["products"][0]["name"] == "Enamel mug"


def test_an_agent_from_a_plugin_template_reads_proposes_and_its_cleared_action_runs_once() -> None:
    _switch(True, currency="EUR")
    with _db() as db:
        design, playbook = agents.from_template("hello_shop.merchandiser")
        key = agents.create(db, design, by="owner@tower.example", template="hello_shop.merchandiser", playbook=playbook).key
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, key, config.AgentSettings(enabled=True, tier="fast", ai_daily_usd=1.0, tools=list(registry.tools_of(registry.AGENTS[key]))))
    model = Scripted([
        ("hello_shop_orders", {"day": "today"}),
        ("propose", {"action_type": "hello_shop.feature", "title": "Feature the apron", "payload_json": '{"product_id": "p3"}'}),
        ("propose", {"action_type": "hello_shop.feature", "title": "Feature the mug", "payload_json": '{"product_id": "p2", "why": "1 order today"}'}),
        ("propose", {"action_type": "hello_shop.feature", "title": "The mug again", "payload_json": '{"product_id": "p2"}'}),
        ("propose", {"action_type": "hello_shop.feature", "title": "No id", "payload_json": "{}"}),
    ])
    runner.use_model(model)
    assert runner.run(_db, key, trigger="manual").status == "done"
    orders = model.results[0]["orders"]
    assert len(orders) == 2 and {o["email"] for o in orders} == {plugins.HIDDEN} and orders[0]["country"] == "PT"
    assert "sold out" in model.results[1]["error"]
    assert model.results[2]["status"] == "waiting for the owner's approval"
    assert "Already waiting" in model.results[3]["error"], "the same product is never proposed twice while the first waits"
    assert "product_id is required" in model.results[4]["error"]
    with _db() as db:
        row = db.execute(select(TeamApproval)).scalar_one()
        assert (row.action_type, row.level) == ("hello_shop.feature", "L1")
        approvals.decide(db, [row], "approve", admin_id=1)
        assert approvals.execute_due(db) == 1
        assert db.get(TeamApproval, row.id).result == {"featured": ["p2"]}  # type: ignore[union-attr]
        event = db.execute(select(TeamEvent).where(TeamEvent.type == "hello_shop.featured")).scalar_one()
        assert event.source == "plugin:hello_shop" and event.payload["approval_id"] == row.id
        assert approvals.execute_due(db) == 0, "carried out once"
        assert approvals.CHECKS["hello_shop.feature"](db, {"product_id": "p2"}) == ["Enamel mug is on the front page already."]


def test_a_cleared_action_of_a_plugin_switched_off_fails_with_the_reason() -> None:
    _switch(True, currency="EUR")
    with _db() as db:
        key = agents.create(db, agents.from_template("hello_shop.merchandiser")[0], by="owner@tower.example").key
        row = approvals.propose(db, agent=key, action_type="hello_shop.feature", title="Mug", payload={"product_id": "p2"})
        approvals.decide(db, [row], "approve", admin_id=1)
        row_id = row.id
    _switch(False)
    with _db() as db:
        approvals.execute_due(db)
        done = db.get(TeamApproval, row_id)
        assert done is not None and done.status == "failed" and "switched off" in done.result["error"]
        with pytest.raises(approvals.ProposalRefused, match="switched off"):
            approvals.propose(db, agent=key, action_type="hello_shop.feature", title="Tote", payload={"product_id": "p1"})


def test_the_plugins_numbers_reach_insights() -> None:
    _switch(True, currency="EUR")
    with _db() as db:
        assert plugins.collect(db) == {"hello_shop": []}
        rows = {(r.metric, r.dimension): r.value for r in db.execute(select(KpiDaily).where(KpiDaily.metric.like("plugin.hello_shop.%"))).scalars()}
        assert rows == {("plugin.hello_shop.orders", "PT"): 1.0, ("plugin.hello_shop.orders", "DE"): 1.0, ("plugin.hello_shop.sold_out", ""): 1.0}
        assert queries.kpi(db, queries.BY_KEY["hello_shop_orders"], 7)["value"] == 2.0
        assert plugins.collect(db) == {"hello_shop": []}
        assert db.execute(select(KpiDaily).where(KpiDaily.metric == "plugin.hello_shop.orders")).scalars().all().__len__() == 2, "replaced, not added"
        assert plugins.state()["hello_shop"]["collected"]["errors"] == []


def test_broken_plugins_are_reported_and_the_rest_still_load(tmp_path: Path) -> None:
    def folder(name: str, code: str) -> None:
        (tmp_path / name).mkdir()
        (tmp_path / name / "plugin.py").write_text(code)

    good = "from app.plugin_api import Plugin\nplugin = Plugin(key='good_one', name='Good', version='1', api='1.0')\n"
    folder("good_one", good)
    folder("crashes", "raise RuntimeError('no database')\n")
    folder("no_plugin", "x = 1\n")
    folder("other_key", good.replace("good_one", "something_else"))
    folder("Bad-Name", good)
    found = loader.discover(tmp_path)
    ok = [f for f in found if isinstance(f, loader.Found)]
    failed = dict(f for f in found if isinstance(f, tuple))
    assert [f.plugin.key for f in ok] == ["good_one"]
    assert "RuntimeError: no database" in failed["crashes"] and "plugin = Plugin" in failed["no_plugin"]
    assert "folder's name" in failed["other_key"] and "folder name" in failed["Bad-Name"]
    with pytest.raises(plugins.PluginError, match="2.x|speaks 1"):
        plugins.validate(Plugin(key="future", name="F", version="1", api="2.0"))
    clash = Plugin(key="hello", name="H", version="1", api="1.0", tools=(Tool("shop_products", "x", lambda ctx: {}),))
    with pytest.raises(plugins.PluginError, match="taken already"):
        plugins.register(clash, "python", "-")
    with pytest.raises(plugins.PluginError, match="target"):
        plugins.validate(Plugin(key="t1", name="T", version="1", api="1.0",
                                actions=(Action("a1", "A", lambda c, p, i: {}, fields={"x": Param("string")}, target="y"),)))
    assert "hello" not in plugins.LOADED


def test_signatures_bind_time_path_and_body() -> None:
    header = signing.sign("s3cret", "/plugin-api/v1/x/events", b"{}", now=1000)
    assert signing.verify("s3cret", "/plugin-api/v1/x/events", b"{}", header, now=1100)
    assert not signing.verify("s3cret", "/plugin-api/v1/x/events", b"{}", header, now=1000 + signing.TOLERANCE + 1)
    assert not signing.verify("s3cret", "/plugin-api/v1/y/events", b"{}", header, now=1000)
    assert not signing.verify("s3cret", "/plugin-api/v1/x/events", b"{ }", header, now=1000)
    assert not signing.verify("other", "/plugin-api/v1/x/events", b"{}", header, now=1000)
    assert not signing.verify("s3cret", "/p", b"", "garbage")


class FakeProject:
    """A project speaking the plugin interface over HTTP, refusing anything not signed with its secret."""

    MANIFEST: dict[str, Any] = {
        "api": "1.0", "key": "crm", "name": "Tiny CRM", "version": "0.3.0", "personal": ["email", "phone"],
        "tools": [{"key": "customers", "description": "Customers by plan.", "params": {"plan": {"type": "string"}}, "view": True}],
        "actions": [{"key": "tag", "label": "Tag a customer", "ceiling": "L3", "fields": {"customer_id": {"type": "integer", "required": True},
                                                                                           "tag": {"type": "string", "required": True}},
                     "target": "customer_id", "check": True}],
        "metrics": [{"key": "customers", "label": "Customers", "agg": "last"}],
        "templates": [{"key": "account_manager", "name": "Account Manager", "department": "Sales",
                       "job": "Looks after the customers on the paid plans and proposes tags for follow-up.",
                       "tools": ["customers"], "actions": ["tag"], "kpi": "customers"}],
    }

    def __init__(self) -> None:
        self.secret = ""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.executed: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        if not signing.verify(self.secret, request.url.path, body, request.headers.get(signing.HEADER, "")):
            return httpx.Response(401, json={"error": "bad signature"})
        data = json.loads(body) if body else {}
        path = request.url.path.removeprefix("/ct")
        self.calls.append((path, data))
        if path == "/manifest":
            return httpx.Response(200, json=self.MANIFEST)
        if path == "/tools/customers":
            return httpx.Response(200, json={"customers": [{"id": 7, "plan": data["args"].get("plan", "pro"), "email": "x@y.z", "phone": "1"}]})
        if path == "/actions/tag/check":
            return httpx.Response(200, json={"problems": [] if data["payload"]["customer_id"] == 7 else ["No such customer."]})
        if path == "/actions/tag/execute":
            if data["approval_id"] in self.executed:
                return httpx.Response(409, json={"error": "done already"})
            self.executed.append(data["approval_id"])
            return httpx.Response(200, json={"tagged": data["payload"]["customer_id"]})
        if path == "/metrics":
            return httpx.Response(200, json={"values": {"customers": 41}})
        return httpx.Response(404, json={"error": "no such address"})


def test_a_project_connected_over_http_works_like_a_plugin_and_everything_is_signed(admin: Any) -> None:  # noqa: F811
    project = FakeProject()
    connector.TRANSPORT = httpx.MockTransport(project)
    assert admin.post("/admin-api/plugins/connections", json={"key": "crm", "base_url": "https://crm.example/ct?x=1"}).status_code == 422
    added = admin.post("/admin-api/plugins/connections", json={"key": "crm", "base_url": "https://crm.example/ct/"}).json()
    project.secret = added["secret"]
    assert len(project.secret) >= 32
    listed = admin.get("/admin-api/plugins").json()
    assert any(p["key"] == "crm" and "Fetch" in p["error"] for p in listed["problems"]) and project.secret not in json.dumps(listed)
    fetched = admin.post("/admin-api/plugins/connections/crm/fetch")
    assert fetched.status_code == 200, fetched.text
    crm = next(p for p in fetched.json()["plugins"] if p["key"] == "crm")
    assert crm["source"] == "http" and crm["enabled"] is False and [t["key"] for t in crm["tools"]] == ["crm_customers"]
    assert admin.put("/admin-api/plugins/crm", json={"enabled": True}).status_code == 200
    plugins.ensure()
    with _db() as db:
        assert "crm.account_manager" in {t.key for t in templates.offered()}
        key = agents.create(db, agents.from_template("crm.account_manager")[0], by="owner@tower.example").key
        result = tools.CATALOGUE["crm_customers"][0](type("Ctx", (), {"agent": registry.AGENTS[key], "db": _db})(), plan="pro")
        assert result["customers"][0] == {"id": 7, "plan": "pro", "email": plugins.HIDDEN, "phone": plugins.HIDDEN}
        assert project.calls[-1] == ("/tools/customers", {"args": {"plan": "pro"}, "agent": key})
        with pytest.raises(approvals.ProposalRefused, match="No such customer"):
            approvals.propose(db, agent=key, action_type="crm.tag", title="Tag 8", payload={"customer_id": 8, "tag": "vip"})
        row = approvals.propose(db, agent=key, action_type="crm.tag", title="Tag 7", payload={"customer_id": 7, "tag": "vip"})
        assert row.level == "L3"
        approvals.decide(db, [row], "approve", admin_id=1)
        approvals.execute_due(db)
        assert db.get(TeamApproval, row.id).result == {"tagged": 7} and project.executed == [row.id]  # type: ignore[union-attr]
        assert plugins.collect(db, only="crm") == {"crm": []}
        assert queries.kpi(db, queries.BY_KEY["crm_customers"], 1)["value"] == 41.0
    view = admin.post("/admin-api/plugins/crm/views/customers").json()
    assert view["result"]["customers"][0]["email"] == plugins.HIDDEN
    # The project tells the team something happened: signed, or refused.
    body = json.dumps({"events": [{"type": "signup", "payload": {"plan": "pro", "email": "new@y.z"}}]}).encode()
    path = "/plugin-api/v1/crm/events"
    assert admin.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert admin.post(path, content=b"x" * 70_000, headers={signing.HEADER: signing.sign(project.secret, path, b"x" * 70_000)}).status_code == 413
    assert admin.post(path, content=body, headers={signing.HEADER: signing.sign("wrong-secret", path, body)}).status_code == 401
    assert admin.post(path, content=body, headers={signing.HEADER: signing.sign(project.secret, path, body, now=1)}).status_code == 401, "stale"
    assert admin.post(path, content=body, headers={signing.HEADER: signing.sign(project.secret, path, body)}).status_code == 202
    with _db() as db:
        event = db.execute(select(TeamEvent).where(TeamEvent.type == "crm.signup")).scalar_one()
        assert event.payload == {"plan": "pro", "email": plugins.HIDDEN} and event.source == "plugin:crm"
    # A renewed secret: the old one stops working at once.
    renewed = admin.post("/admin-api/plugins/crm/secret").json()["secret"]
    assert renewed != project.secret
    assert admin.post(path, content=body, headers={signing.HEADER: signing.sign(project.secret, path, body)}).status_code == 401
    gone = admin.delete("/admin-api/plugins/connections/crm").json()
    assert all(p["key"] != "crm" for p in gone["plugins"]) and "crm_customers" not in tools.CATALOGUE and "crm.tag" not in registry.ACTIONS
    log = admin.get("/admin-api/audit").text
    assert all(a in log for a in ("plugin.connection_added", "plugin.manifest_fetched", "plugin.changed", "plugin.secret_renewed", "plugin.connection_removed"))
    assert project.secret not in log and renewed not in log


def test_the_owner_switches_a_plugin_on_only_with_its_settings(admin: Any) -> None:  # noqa: F811
    refused = admin.put("/admin-api/plugins/hello_shop", json={"enabled": True})
    assert refused.status_code == 422 and "Currency" in refused.text
    assert not plugins.enabled("hello_shop"), "nothing is saved when the change is refused"
    assert admin.put("/admin-api/plugins/hello_shop", json={"values": {"nope": "x"}}).status_code == 422
    on = admin.put("/admin-api/plugins/hello_shop", json={"enabled": True, "values": {"currency": "EUR"}}).json()
    shop = next(p for p in on["plugins"] if p["key"] == "hello_shop")
    assert shop["enabled"] and shop["missing"] == [] and shop["settings"][0]["value"] == "EUR" and shop["personal"] == ["email"]
    assert [v["key"] for v in shop["views"]] == ["products", "orders"]
    view = admin.post("/admin-api/plugins/hello_shop/views/orders").json()
    assert {o["email"] for o in view["result"]["orders"]} == {plugins.HIDDEN}
    assert admin.post("/admin-api/plugins/hello_shop/collect").json() == {"problems": {"hello_shop": []}}
    assert admin.post("/admin-api/plugins/nothing/collect").status_code == 404
