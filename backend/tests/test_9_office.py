"""The HQ: the office shows who works, who visits whom with a handoff and whom the owner
talks to — from what the team records."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import (
    NiaConversation,
    PlatformSetting,
    TeamConversation,
    TeamEvent,
    TeamMessage,
    TeamRun,
)
from app.services.office import state as office
from app.services.team import config, registry, runner
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        for model in (TeamRun, TeamEvent, TeamConversation, NiaConversation):
            db.execute(delete(model))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
    runner.use_model(runner.GeminiModel())


def _ready(*keys: str) -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        for key in keys:
            config.save_agent(db, key, config.AgentSettings(enabled=True, tier="fast", ai_daily_usd=1.0, tools=list(registry.tools_of(registry.AGENTS[key]))))


def _agent(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(a for a in data["agents"] if a["key"] == key)


def test_who_works_who_visits_and_where_the_owner_is() -> None:
    _ready("qa_runner", "product_analyst")
    now = datetime.now(UTC)
    with _db() as db:
        db.add(TeamRun(agent="qa_runner", trigger="schedule", status="running", started_at=now - timedelta(minutes=2),
                       tool_calls=[{"tool": "read_insights"}, {"tool": "web_search"}]))
        db.add(TeamRun(agent="product_analyst", trigger="schedule", status="running", started_at=now - timedelta(hours=3), tool_calls=[]))
        db.add(TeamRun(agent="product_analyst", trigger="schedule", status="done", started_at=now - timedelta(hours=4), finished_at=now - timedelta(hours=4),
                       summary="What I did:\n- Measured 3 ideas\nmore", tool_calls=[]))
        type_ = registry.AGENTS["qa_runner"].emits[0]
        db.add(TeamEvent(type=type_, source="qa_runner", payload={}, created_at=now - timedelta(minutes=1)))
        db.add(TeamEvent(type=type_, source="qa_runner", payload={}, created_at=now - timedelta(minutes=20)))
        talk = TeamConversation(agent="product_analyst", title="", created_at=now, updated_at=now)
        db.add(talk)
        db.flush()
        db.add(TeamMessage(conversation_id=talk.id, role="owner", text="hi", attachment_ids=[], proposal_ids=[], tool_calls=[], created_at=now))
        data = office.state(db, now)
    mi, outreach = _agent(data, "qa_runner"), _agent(data, "product_analyst")
    assert mi["mode"] == "working" and mi["doing"] == "Searching the web"
    woken = [k for k in (a.key for a in registry.AGENTS.values() if type_ in a.subscribes) if k != "qa_runner"]
    assert mi["visiting"] == {"to": woken[0], "about": type_, "at": mi["visiting"]["at"]}, "the newest handoff takes it to a colleague"
    assert outreach["mode"] == "idle" and outreach["doing"] == "Measured 3 ideas", "a run cut off long ago is not work"
    assert _agent(data, "trust_compliance")["mode"] == "off"
    assert data["owner"]["with_agent"] == "product_analyst"
    assert len(data["handoffs"]) == 2


def test_the_owner_leaves_after_a_while_and_nia_counts_too() -> None:
    now = datetime.now(UTC)
    with _db() as db:
        talk = TeamConversation(agent="product_analyst", title="", created_at=now, updated_at=now)
        db.add(talk)
        db.flush()
        db.add(TeamMessage(conversation_id=talk.id, role="owner", text="hi", attachment_ids=[], proposal_ids=[], tool_calls=[], created_at=now - timedelta(minutes=30)))
        assert office.state(db, now)["owner"]["with_agent"] is None
        # A minute after the conversation went quiet the owner is back in the suite; an answer still coming keeps them there.
        db.add(TeamMessage(conversation_id=talk.id, role="owner", text="hi", attachment_ids=[], proposal_ids=[], tool_calls=[], created_at=now - timedelta(seconds=70)))
        assert office.state(db, now)["owner"]["with_agent"] is None
        db.add(TeamMessage(conversation_id=talk.id, role="agent", text="answer", attachment_ids=[], proposal_ids=[], tool_calls=[], created_at=now - timedelta(seconds=20)))
        assert office.state(db, now)["owner"]["with_agent"] == "product_analyst"
        db.execute(delete(TeamMessage))
        from app.models import NiaMessage

        nia = NiaConversation(title="", created_at=now, updated_at=now)
        db.add(nia)
        db.flush()
        db.add(NiaMessage(conversation_id=nia.id, role="owner", text="hi", tool_calls=[], created_at=now))
        assert office.state(db, now)["owner"]["with_agent"] == "nia"


def test_a_run_shows_its_tools_while_it_goes_on() -> None:
    _ready("product_analyst")
    seen: list[list[str]] = []

    class Model:
        def converse(self, *, call_tool: Any, before_round: Any, on_usage: Any, **_: Any) -> str:
            call_tool("read_insights", {"days": 7})
            with _db() as db:
                run = db.execute(select(TeamRun).where(TeamRun.agent == "product_analyst", TeamRun.status == "running")).scalar_one()
                seen.append([c["tool"] for c in run.tool_calls])
                seen.append([_agent(office.state(db), "product_analyst")["doing"]])
            return "Done."

    runner.use_model(Model())
    runner.run(_db, "product_analyst", trigger="manual")
    assert seen == [["read_insights"], ["Reading the numbers"]]


def test_the_office_in_the_admin_app(admin: Any) -> None:  # noqa: F811 - the fixture
    r = admin.get("/admin-api/office")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["agents"]) == len(registry.AGENTS) and {"now", "owner", "handoffs"} <= set(body)
