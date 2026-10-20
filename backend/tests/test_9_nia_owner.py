"""The owner's Nia (agent team, stage A2): it answers only when the owner has set it up, speaks
for the team from its tools, remembers what the owner decides, and turns instructions into
directives that reach the agents only after the owner confirms them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import (
    AIUsage,
    NiaConversation,
    NiaMemory,
    OwnerDirective,
    PlatformSetting,
    TeamAgentState,
    TeamApproval,
    TeamEvent,
    TeamRun,
)
from app.services.team import config, registry, runner
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    with _db() as db:
        for model in (OwnerDirective, NiaMemory, NiaConversation, TeamApproval, TeamRun, TeamEvent, TeamAgentState):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
    yield
    runner.use_model(runner.GeminiModel())


def _ready() -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        for key in ("nia", "qa_runner"):
            config.save_agent(db, key, config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0, "tools": list(registry.tools_of(registry.AGENTS[key]))}))


class Scripted:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], answer: str) -> None:
        self.calls, self.answer, self.results = calls, answer, []  # type: list[dict[str, Any]]
        self.system = self.prompt = ""

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_more: Any) -> str:
        self.system, self.prompt = system, prompt
        for name, args in self.calls:
            before_round()
            on_usage(runner.Usage("fake-model", 500, 200, 5))
            self.results.append(call_tool(name, args))
        return self.answer


def test_nia_says_what_it_waits_for(admin: Any) -> None:  # noqa: F811 - the fixture
    status = admin.get("/admin-api/nia/status").json()
    assert status["ready"] is False and "switched off" in status["problem"]
    conversation = admin.post("/admin-api/nia/conversations").json()
    refused = admin.post(f"/admin-api/nia/conversations/{conversation['id']}/messages", json={"text": "Give me the rundown"})
    assert refused.status_code in (400, 422) and "switched off" in refused.text


def test_the_rundown_memory_and_a_directive_that_only_counts_once_confirmed(admin: Any) -> None:  # noqa: F811 - the fixture
    _ready()
    admin.post("/admin-api/nia/memories", json={"kind": "decision", "text": "No discounts above 20 percent.", "evidence": "owner, 4 Oct"})
    model = Scripted([
        ("team_overview", {}),
        ("read_insights", {"days": 7}),
        ("remember", {"kind": "preference", "text": "Rundowns start with the cleared proposals."}),
        ("propose_directive", {"text": "Check every page of the shop this week.", "agents": ["qa_runner"], "goal": "All pages checked"}),
    ], "Here is where we stand. I wrote down the directive for the QA Runner; please confirm it.")
    runner.use_model(model)
    conversation = admin.post("/admin-api/nia/conversations").json()
    said = admin.post(f"/admin-api/nia/conversations/{conversation['id']}/messages", json={"text": "Give me the rundown, and have the shop pages checked this week."})
    assert said.status_code == 200, said.text
    body = said.json()
    assert [m["role"] for m in body["conversation"]["messages"]] == ["owner", "nia"] and body["conversation"]["title"].startswith("Give me the rundown")
    assert "No discounts above 20 percent" in model.system, "Nia answers with what it remembers"
    overview = model.results[0]
    assert {a["agent"] for a in overview["agents"]} == set(registry.AGENTS) and next(a for a in overview["agents"] if a["agent"] == "nia")["status"] == "ready"
    assert set(model.results[1]) == {"days", "kpis"}
    directive = body["directives"][0]
    assert directive["status"] == "proposed" and directive["agent_names"] == ["QA Runner"]
    with _db() as db:
        assert db.execute(select(TeamEvent).where(TeamEvent.type == "owner.directive")).first() is None, "nothing reaches the agents before the owner confirms"
        usage = db.execute(select(AIUsage).where(AIUsage.operation == "agent.nia.chat")).scalars().all()
        assert len(usage) == 4
    memories = admin.get("/admin-api/nia/memories").json()
    assert {m["kind"] for m in memories} == {"decision", "preference"}
    confirmed = admin.post(f"/admin-api/nia/directives/{directive['id']}/confirm").json()
    assert confirmed["status"] == "active"
    # The agent sees the directive in its next run and reports on it.
    agent_model = Scripted([("report_directive", {"directive_id": directive["id"], "progress": "3 of 10 addresses checked."})], "Working on the owner's directive.")
    runner.use_model(agent_model)
    outcome = runner.run(_db, "qa_runner", trigger="event")
    assert outcome.status == "done" and "Check every page of the shop" in agent_model.prompt and agent_model.results[0] == {"reported": True}
    report = admin.get("/admin-api/nia/directives").json()[0]["reports"][0]
    assert report["agent"] == "qa_runner" and report["text"].startswith("3 of 10")
    assert admin.post(f"/admin-api/nia/directives/{directive['id']}/close", json={"status": "done"}).json()["status"] == "done"
    # Nia's own scheduled run (the brief) sees its memory too.
    brief_model = Scripted([], "Rundown: …")
    runner.use_model(brief_model)
    runner.run(_db, "nia", trigger="schedule")
    assert "Rundowns start with the cleared proposals." in brief_model.prompt
    assert admin.get("/admin-api/nia/status").json()["brief"]["summary"] == "Rundown: …"


def test_the_owner_edits_and_deletes_memories(admin: Any) -> None:  # noqa: F811 - the fixture
    made = admin.post("/admin-api/nia/memories", json={"kind": "lesson", "text": "Video posts get the most sign-ups.", "evidence": "3 of 10"}).json()
    changed = admin.put(f"/admin-api/nia/memories/{made['id']}", json={"kind": "lesson", "text": "Video posts get many more sign-ups.", "evidence": "3 of 10"}).json()
    assert changed["text"].endswith("more sign-ups.")
    assert [m["id"] for m in admin.get("/admin-api/nia/memories?q=video").json()] == [made["id"]]
    assert admin.delete(f"/admin-api/nia/memories/{made['id']}").status_code == 204
    assert admin.get("/admin-api/nia/memories").json() == []
    hand = admin.post("/admin-api/nia/directives", json={"text": "Pause the summer campaign.", "agents": ["trust_compliance"]})
    assert hand.status_code == 201 and hand.json()["status"] == "active"
    assert admin.post("/admin-api/nia/directives", json={"text": "x", "agents": ["nobody"]}).status_code in (400, 422)
