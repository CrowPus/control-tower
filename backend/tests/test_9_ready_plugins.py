"""The plugins every install ships (plugins/): Stripe and Database, both read-only and off until the owner switches
them on; a plugin whose parts follow its settings (the database's named queries) is rebuilt when they change."""

from __future__ import annotations

import importlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import transaction
from app.models import KpiDaily, PlatformSetting
from app.services.insights import queries
from app.services.plugins import core as plugins
from app.services.team import tools
from tests.test_9_team import admin  # noqa: F401 - the signed-in owner (module fixture)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with transaction(write=True) as db:
            db.execute(delete(KpiDaily).where(KpiDaily.metric.like("plugin.%")))
            db.execute(delete(PlatformSetting).where(PlatformSetting.key == "plugins"))
            plugins.save_state(db, {})

    plugins.ensure()
    wipe()
    yield
    wipe()
    stripe_module().TRANSPORT = None


def stripe_module() -> Any:
    return importlib.import_module("ct_plugin_stripe")


def _set(key: str, enabled: bool = True, **values: str) -> None:
    from app.core import crypto

    secret = {s.key for s in plugins.LOADED[key].plugin.settings if s.secret}
    with transaction(write=True) as db:
        state = {k: dict(v) for k, v in plugins.state().items()}
        state[key] = {"enabled": enabled, "values": {k: ({"enc": crypto.encrypt(v, associated_data=f"plugin:{key}:{k}")} if k in secret else v)
                                                     for k, v in values.items()}}
        plugins.save_state(db, state)


def test_the_ready_plugins_ship_off() -> None:
    assert {"stripe", "database"} <= set(plugins.LOADED)
    assert not plugins.enabled("stripe") and not plugins.enabled("database")
    assert plugins.is_off("stripe_payments") and "stripe.revenue_watcher" not in {t.key for t in __import__("app.services.team.templates", fromlist=["x"]).offered()}
    assert plugins.LOADED["stripe"].plugin.actions == (), "Stripe is read-only: no actions"


class FakeStripe:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        now = int(datetime.now(UTC).timestamp())

        def price(amount: int, currency: str, interval: str) -> dict[str, Any]:
            return {"id": f"price_{amount}", "unit_amount": amount, "currency": currency, "recurring": {"interval": interval, "interval_count": 1}}

        self.subs = [
            {"id": "sub_1", "customer": "cus_1", "status": "active", "start_date": now, "current_period_end": now, "items": {"data": [{"price": price(1000, "eur", "month"), "quantity": 1}]}},
            {"id": "sub_2", "customer": "cus_2", "status": "active", "start_date": now, "current_period_end": now, "items": {"data": [{"price": price(12000, "eur", "year"), "quantity": 1}]}},
            {"id": "sub_3", "customer": "cus_3", "status": "active", "start_date": now, "current_period_end": now, "items": {"data": [{"price": price(500, "usd", "month"), "quantity": 2}]}},
        ]
        self.charges = [
            {"id": "ch_1", "customer": "cus_1", "amount": 1000, "amount_refunded": 0, "currency": "eur", "status": "succeeded", "created": now,
             "receipt_email": "ana@example.com", "billing_details": {"name": "Ana", "email": "ana@example.com"}},
            {"id": "ch_2", "customer": "cus_2", "amount": 2500, "amount_refunded": 500, "currency": "eur", "status": "succeeded", "created": now},
            {"id": "ch_3", "customer": "cus_4", "amount": 900, "amount_refunded": 0, "currency": "eur", "status": "failed", "created": now, "failure_message": "Card declined"},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        rows = self.subs if request.url.path == "/v1/subscriptions" else self.charges
        after = request.url.params.get("starting_after")
        # Two pages: the first item, then the rest.
        if after is None:
            return httpx.Response(200, json={"data": rows[:1], "has_more": len(rows) > 1})
        return httpx.Response(200, json={"data": rows[1:], "has_more": False})


def test_stripe_reads_subscriptions_payments_and_revenue_and_hides_customers_details() -> None:
    fake = FakeStripe()
    stripe_module().TRANSPORT = httpx.MockTransport(fake)
    _set("stripe", api_key="rk_test_0123456789abcdef", currency="eur")
    assert plugins.LOADED["stripe"].warnings == []
    assert queries.BY_KEY["stripe_mrr"].label == "Monthly recurring revenue (EUR) (Stripe)", "labels follow the owner's currency"
    payments = tools.CATALOGUE["stripe_payments"][0](None, days=7)
    assert payments["count"] == 3 and payments["failed"] == 1
    first = payments["shown"][0]
    assert first["receipt_email"] == plugins.HIDDEN and first["billing_details"] == plugins.HIDDEN and first["amount"] == 10.0
    assert fake.requests[0].headers["authorization"].startswith("Basic ")
    assert fake.requests[1].url.params["starting_after"] == "ch_1", "all pages are read"
    revenue = tools.CATALOGUE["stripe_revenue"][0](None, days=30)
    assert revenue["by_currency"]["eur"] == {"paid": 35.0, "refunded": 5.0, "payments": 2, "net": 30.0}
    subs = tools.CATALOGUE["stripe_subscriptions"][0](None, status="active")
    assert subs["total"] == 3 and subs["shown"][1]["monthly"] == {"eur": 10.0}
    with transaction(write=True) as db:
        assert plugins.collect(db, only="stripe") == {"stripe": []}
        values = {r.metric: r.value for r in db.execute(select(KpiDaily).where(KpiDaily.metric.like("plugin.stripe.%"))).scalars()}
    assert values == {"plugin.stripe.mrr": 20.0, "plugin.stripe.active_subscriptions": 3.0, "plugin.stripe.failed_payments": 1.0, "plugin.stripe.revenue": 30.0}


def test_stripe_errors_reach_the_agent_as_a_failure_not_a_guess() -> None:
    stripe_module().TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "Invalid API Key provided"}}))
    _set("stripe", api_key="rk_test_wrong", currency="usd")
    with pytest.raises(RuntimeError, match="Stripe answered 401"):
        tools.CATALOGUE["stripe_payments"][0](None)


def _shop_db(tmp: Path) -> Path:
    path = tmp / "shop.db"
    with sqlite3.connect(path) as db:
        db.execute("create table users (id integer primary key, email text, plan text, created_at text)")
        db.executemany("insert into users (email, plan, created_at) values (?, ?, ?)",
                       [("a@x.io", "pro", "2026-10-19"), ("b@x.io", "free", "2026-10-20"), ("c@x.io", "pro", "2026-10-20")])
    return path


QUERIES = [
    {"key": "users_by_plan", "description": "Users of one plan.", "sql": "select id, email, plan from users where plan = :plan", "params": {"plan": "string"}},
    {"key": "users_total", "description": "Users in total.", "sql": "select count(*) from users", "metric": "last"},
    {"key": "newest", "description": "The newest users.", "sql": "select id, email, created_at from users order by id desc"},
]


def test_the_database_plugin_runs_only_the_owners_queries_read_only(tmp_path: Path) -> None:
    path = _shop_db(tmp_path)
    assert plugins.LOADED["database"].tools == ()
    _set("database", url=f"sqlite:///{path}", queries=json.dumps(QUERIES), personal="plan_secret")
    loaded = plugins.LOADED["database"]
    assert loaded.warnings == [] and loaded.tools == ("database_users_by_plan", "database_users_total", "database_newest")
    assert loaded.kpis == ("database_users_total",)
    pro = tools.CATALOGUE["database_users_by_plan"][0](None, plan="pro")
    assert [r["id"] for r in pro["rows"]] == [1, 3] and {r["email"] for r in pro["rows"]} == {plugins.HIDDEN}
    assert "plan is required" in tools.CATALOGUE["database_users_by_plan"][0](None)["error"]
    with transaction(write=True) as db:
        assert plugins.collect(db, only="database") == {"database": []}
        assert db.execute(select(KpiDaily.value).where(KpiDaily.metric == "plugin.database.users_total")).scalar_one() == 3.0
    # The connection itself is read-only, whatever slips through.
    database = importlib.import_module("ct_plugin_database")
    from sqlalchemy import text

    with pytest.raises(Exception, match="readonly|read-only|read only"), database._engine(f"sqlite:///{path}").connect() as c:
        c.execute(text("delete from users"))


@pytest.mark.parametrize(("sql", "words"), [
    ("delete from users", "starts with SELECT"),
    ("select 1; delete from users", "One query only"),
    ("with x as (delete from users returning *) select * from x", "only reads"),
    ("select * into backup from users", "only reads"),
])
def test_queries_that_would_write_are_refused(sql: str, words: str) -> None:
    database = importlib.import_module("ct_plugin_database")
    with pytest.raises(ValueError, match=words):
        database.check_sql(sql)
    assert database.check_sql("select 'delete me' as note from users") is None, "words inside text are fine"


def test_settings_that_cannot_be_used_keep_the_plugin_and_say_why(admin: Any) -> None:  # noqa: F811
    bad = admin.put("/admin-api/plugins/database", json={"values": {"url": "sqlite:///nowhere.db", "queries": '[{"key": "x1", "description": "d", "sql": "drop table users"}]'}})
    assert bad.status_code == 200
    db_plugin = next(p for p in bad.json()["plugins"] if p["key"] == "database")
    assert db_plugin["tools"] == [] and any("starts with SELECT" in w for w in db_plugin["warnings"])
    good = admin.put("/admin-api/plugins/database", json={"values": {"queries": json.dumps(QUERIES[1:2])}}).json()
    db_plugin = next(p for p in good["plugins"] if p["key"] == "database")
    assert [t["key"] for t in db_plugin["tools"]] == ["database_users_total"] and db_plugin["warnings"] == []
    assert db_plugin["settings"][0]["value"] is None and db_plugin["settings"][0]["set"], "the address is a secret: never sent back"
