"""The team's brain: one graph of what the agents know and did, read by the owner in Admin → Brain and by every
agent through search_brain."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import (
    AIUsage,
    NiaMemory,
    OwnerDirective,
    PlatformSetting,
    TeamAgentState,
    TeamApproval,
)
from app.services.brain import graph as brain
from app.services.team import config, events, registry, runner, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@pytest.fixture
def seeded() -> Iterator[dict[str, int]]:
    """A proposal, a note that mentions it, memories and a directive."""
    now = datetime.now(UTC)
    with _db() as db:
        proposal = TeamApproval(agent="nia", action_type="product.idea", level="L1", title="Show the pricing page first on mobile",
                                payload={"title": "Show the pricing page first on mobile"}, status="approved", evidence=[], result={}, created_at=now)
        db.add(proposal)
        db.flush()
        state = events.state(db, "nia")
        state.notes = [{"text": f"The pricing idea #{proposal.id} needs its numbers measured after two weeks", "evidence": "insights", "at": now.isoformat(), "run_id": 1}]
        person = NiaMemory(kind="person", topic="owner", text="The owner is called Brainy Testperson", active=True, created_at=now, updated_at=now)
        rule = NiaMemory(kind="decision", topic="pricing", text="Prices change only on Mondays", active=True, created_at=now, updated_at=now)
        diary = NiaMemory(kind="work", topic="browser", text="Logged in to LinkedIn as owner@example.com", active=True, created_at=now, updated_at=now)
        db.add_all([person, rule, diary])
        directive = OwnerDirective(text=f"Measure the pricing idea #{proposal.id} this week", agents=["nia"], status="active", reports=[], created_at=now)
        db.add(directive)
        db.flush()
        ids = {"proposal": proposal.id, "person": person.id, "rule": rule.id, "diary": diary.id, "directive": directive.id}
    yield ids
    with _db() as db:
        db.execute(delete(TeamApproval))
        db.execute(delete(NiaMemory).where(NiaMemory.id.in_((ids["person"], ids["rule"], ids["diary"]))))
        db.execute(delete(OwnerDirective).where(OwnerDirective.id == ids["directive"]))
        db.execute(delete(TeamAgentState))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
    runner.use_model(runner.ProviderModel())


def _links(g: brain.Graph) -> set[tuple[str, str, str]]:
    return set(g.links)


def test_the_brain_links_what_the_agents_know(seeded: dict[str, int]) -> None:
    with _db() as db:
        g = brain.build(db)
    proposal = f"proposal:{seeded['proposal']}"
    links = _links(g)
    assert ("agent:nia", proposal, "proposed") in links
    assert ("note:nia:0", proposal, "mentions") in links, "a note naming a proposal by its number is linked to it"
    assert (f"directive:{seeded['directive']}", proposal, "mentions") in links and (f"directive:{seeded['directive']}", "agent:nia", "for") in links
    assert ("topic:pricing", f"memory:{seeded['rule']}", "holds") in links and ("agent:nia", "topic:pricing", "remembers") in links
    assert ("agent:nia", "department:Leadership", "works in") in links
    assert len([n for n in g.nodes.values() if n["kind"] == "agent"]) == len(registry.AGENTS)
    assert not {n["kind"] for n in g.nodes.values()} & {"opportunity", "category", "company"}, "no business of Epliv or Role365 in the core"


def test_no_person_becomes_part_of_the_brain(seeded: dict[str, int]) -> None:
    with _db() as db:
        other = brain.build(db, for_agent="ext_1")
        nias = brain.build(db, for_agent="nia")
    assert f"memory:{seeded['person']}" not in other.nodes, "another agent never reads what Nia knows about the owner as a person"
    assert f"memory:{seeded['diary']}" not in other.nodes and f"memory:{seeded['diary']}" in nias.nodes, "nor Nia's diary of the work with the owner"
    assert f"memory:{seeded['person']}" in nias.nodes and f"memory:{seeded['rule']}" in other.nodes


def test_reading_the_brain_changes_nothing(seeded: dict[str, int]) -> None:
    with _db() as db:
        before = db.execute(select(func.count()).select_from(TeamAgentState)).scalar()
        brain.build(db)
        assert db.execute(select(func.count()).select_from(TeamAgentState)).scalar() == before


def test_an_agent_reads_what_a_colleague_knows(seeded: dict[str, int]) -> None:
    agent = registry.AGENTS["nia"]
    ctx = tools.Context(agent=agent, run_id=0, db=_db, granted=tools.usable(agent, list(registry.tools_of(agent))))
    assert "search_brain" in ctx.granted
    found = tools.run(ctx, "search_brain", {"query": "pricing page"})["found"]
    proposal = next(f for f in found if f["id"] == f"proposal:{seeded['proposal']}")
    linked = {(x["id"], x["link"]) for x in proposal["linked"]}
    assert ("note:nia:0", "mentions") in linked and ("agent:nia", "proposed") in linked
    only = tools.run(ctx, "search_brain", {"query": "pricing", "kind": "proposal"})["found"]
    assert [f["kind"] for f in only] == ["proposal"]
    assert tools.run(ctx, "search_brain", {"query": "zzqx nothing"})["found"] == []


class FakeModel:
    def __init__(self, answer: str) -> None:
        self.answer, self.prompt = answer, ""

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_more: Any) -> str:
        assert declarations == [], "the brain only reads: no tools"
        self.prompt = prompt
        before_round()
        on_usage(runner.Usage("fake-model", 800, 120, 5))
        return self.answer


def test_the_owner_explores_and_asks_the_brain(seeded: dict[str, int], admin: Any) -> None:  # noqa: F811 - the fixture
    graph = admin.get("/admin-api/brain").json()
    ids = {n["id"] for n in graph["nodes"]}
    assert f"proposal:{seeded['proposal']}" in ids and graph["counts"]["agent"] == len(registry.AGENTS)
    assert all(link["source"] in ids and link["target"] in ids for link in graph["links"])

    r = admin.post("/admin-api/brain/ask", json={"question": "What do we know about the pricing idea?"})
    assert r.status_code == 422 and "model tier" in r.json()["error"]["message"]

    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, "nia", config.AgentSettings(enabled=True, tier="fast", ai_daily_usd=1.0))
    model = FakeModel(f"Nia proposed it [[proposal:{seeded['proposal']}]], the owner's rule [[memory:{seeded['rule']}]] [[proposal:999999]].")
    runner.use_model(model)
    r = admin.post("/admin-api/brain/ask", json={"question": "What do we know about the pricing idea?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cited"] == [f"proposal:{seeded['proposal']}", f"memory:{seeded['rule']}"] and "999999" not in body["text"], "only real nodes are cited"
    assert f"proposal:{seeded['proposal']}" in body["found"] and "pricing page" in model.prompt
    with _db() as db:
        assert db.execute(select(func.count()).select_from(AIUsage).where(AIUsage.operation == "agent.nia.brain")).scalar() == 1
