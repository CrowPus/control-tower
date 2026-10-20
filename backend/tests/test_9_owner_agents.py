"""The owner's own agents (docs/PLAN.md §4–5): made from the agent template — blank or a starter template — checked
against the rules on every save, run like any other agent under the engine's rules, changed and archived by the owner,
and proposed by Nia only for the owner to clear.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import OwnerAgent, PlatformSetting, TeamApproval, TeamPlaybook, TeamRun
from app.services.team import agents, approvals, config, registry, runner, templates, tools
from tests.test_9_team import (  # noqa: F401, F811 - the signed-in owner (module fixture)
    Scripted,
    admin,
)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


def _design(**change: Any) -> agents.Design:
    base: dict[str, Any] = {"name": "Support Scout", "department": "Support", "job": "Reads the incidents and tells the owner what customers run into.",
                            "tools": ["incidents"], "actions": [], "emits": ["support.note"]}
    return agents.Design.model_validate({**base, **change})


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with _db() as db:
            keys = list(db.execute(select(OwnerAgent.key)).scalars())
            for model in (TeamApproval, TeamRun, TeamPlaybook):
                db.execute(delete(model).where(model.agent.in_([*keys, "nia"])))
            db.execute(delete(OwnerAgent))
            db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
        registry.AGENTS.changed()

    wipe()
    yield
    wipe()
    runner.use_model(runner.ProviderModel())


def test_every_starter_template_keeps_the_rules_and_offers_only_real_tools() -> None:
    assert len(templates.TEMPLATES) >= 6 and all(templates.BY_KEY[t.key] is t for t in templates.TEMPLATES)
    for t in templates.TEMPLATES:
        design, _playbook = agents.from_template(t.key)
        assert agents.check(design) == [], t.key
        assert set(t.tools) <= set(tools.CATALOGUE), t.key
    assert not set(agents.offered_tools()) & agents.NIA_ONLY_TOOLS
    assert "agent.create" not in agents.offered_actions() and "admin.change" not in agents.offered_actions()


@pytest.mark.parametrize(("change", "words"), [
    ({"job": "Helps."}, "job"),
    ({"tools": ["read_insights"]}, "at least one tool"),
    ({"tools": ["incidents", "decide_approvals"]}, "Only Nia"),
    ({"tools": ["incidents", "send_mass_email"]}, "No such tool"),
    ({"actions": ["admin.change"]}, "may propose"),
    ({"actions": ["agent.create"]}, "may propose"),
    ({"actions": ["wire.money"]}, "No such action"),
    ({"emits": ["Support Note"]}, "Event names"),
    ({"kpi": "revenue_we_made_up"}, "Insights numbers"),
    ({"max_rounds": 99}, "rounds"),
])
def test_a_design_that_breaks_a_rule_is_never_saved(change: dict[str, Any], words: str) -> None:
    with _db() as db:
        with pytest.raises(agents.Refused, match=words):
            agents.create(db, _design(**change), by="owner@tower.example")
        assert db.execute(select(OwnerAgent)).first() is None


def test_an_agent_made_from_a_template_starts_off_and_runs_under_the_rules() -> None:
    with _db() as db:
        design, playbook = agents.from_template("researcher")
        row = agents.create(db, design.model_copy(update={"name": "Market Researcher"}), by="owner@tower.example", template="researcher",
                            playbook=playbook or "Look at our three nearest competitors first.")
        key = row.key
        assert key == "market_researcher" and row.template == "researcher"
        assert config.agent(db, key).enabled is False and config.status(db, key)[0] == "off", "a new agent waits for the owner"
        assert approvals.latest_playbook(db, key) is not None
    assert registry.get(key) is not None and registry.get(key).name == "Market Researcher"  # type: ignore[union-attr]
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, key, config.AgentSettings(enabled=True, tier="fast", ai_daily_usd=1.0, tools=["web_search", "read_web_page"]))
    model = Scripted([
        ("web_search", {"query": "competitors"}),
        ("decide_approvals", {"ids": [1], "decision": "approve"}),
        ("propose", {"action_type": "admin.change", "title": "Change a setting", "payload_json": "{}"}),
    ], answer="Searched; nothing to propose.")
    runner.use_model(model)
    outcome = runner.run(_db, key, trigger="manual")
    assert outcome.status == "done"
    assert "decide_approvals" not in model.declared and "error" in model.results[1], "never Nia's tools"
    assert "error" in model.results[2], "never an action outside its design"
    assert "Market Researcher" in model.system and "propose; the owner decides" in model.system


def test_names_become_unique_keys_and_never_a_built_in_one() -> None:
    with _db() as db:
        first = agents.create(db, _design(), by="owner@tower.example")
        second = agents.create(db, _design(), by="owner@tower.example")
        nia = agents.create(db, _design(name="Nia"), by="owner@tower.example")
        assert (first.key, second.key, nia.key) == ("support_scout", "support_scout_2", "my_nia")
        assert registry.AGENTS["nia"].name == "Nia" and registry.AGENTS["my_nia"].department == "Support"


def test_a_changed_design_drops_settings_it_no_longer_allows_and_archiving_withdraws_proposals() -> None:
    with _db() as db:
        key = agents.create(db, _design(tools=["incidents", "qa_history"], actions=["product.idea"]), by="owner@tower.example").key
        config.save_agent(db, key, config.AgentSettings(tools=["incidents", "qa_history"], levels={"product.idea": "L1"}))
        waiting = approvals.propose(db, agent=key, action_type="product.idea", title="Shorter sign-up",
                                    payload={"title": "Shorter sign-up", "problem": "p", "idea": "i", "metric": "cleared", "expect": "up"})
        agents.update(db, key, _design(tools=["incidents"]), by="owner@tower.example")
        assert config.agent(db, key).tools == ["incidents"] and config.agent(db, key).levels == {}
        assert registry.AGENTS[key].actions == ()
        with pytest.raises(agents.Refused, match="built in"):
            agents.update(db, "nia", _design(), by="owner@tower.example")
        agents.archive(db, key)
        assert db.get(TeamApproval, waiting.id).status == "withdrawn"  # type: ignore[union-attr]
        assert config.agent(db, key).enabled is False
        with pytest.raises(agents.Refused):
            agents.archive(db, "nia")
    assert key not in registry.AGENTS and "nia" in registry.AGENTS


def test_nia_proposes_an_agent_and_it_is_made_only_when_the_owner_clears_it() -> None:
    nia = registry.AGENTS["nia"]
    assert "agent.create" in nia.actions and "agent_templates" in nia.tools
    with _db() as db:
        with pytest.raises(approvals.ProposalRefused):
            approvals.propose(db, agent="nia", action_type="agent.create", title="A helper", payload={"name": "Helper", "department": "Ops",
                                                                                                      "job": "short", "tools": ["browse"]})
        row = approvals.propose(db, agent="nia", action_type="agent.create", title="A QA runner for the shop",
                                payload={"template": "qa_runner", "name": "Shop Checker"})
        assert row.level == "L3" and row.status == "pending"
        assert db.execute(select(OwnerAgent)).first() is None, "nothing is made before the owner clears it"
        with pytest.raises(approvals.ProposalRefused):
            approvals.propose(db, agent="nia", action_type="agent.create", title="Again", payload={"template": "qa_runner", "name": "Shop Checker"})
        approvals.decide(db, [row], "approve", admin_id=1)
        assert approvals.execute_due(db) == 1
        made = db.execute(select(OwnerAgent)).scalar_one()
        assert (made.key, made.template, made.department) == ("shop_checker", "qa_runner", "Product")
        assert db.get(TeamApproval, row.id).result == {"agent": "shop_checker", "name": "Shop Checker"}  # type: ignore[union-attr]
        assert config.agent(db, "shop_checker").enabled is False


def test_the_agent_templates_tool_shows_what_an_agent_can_be_made_of() -> None:
    out = tools.CATALOGUE["agent_templates"][0](None)
    assert {t["key"] for t in out["templates"]} == {t.key for t in templates.offered()}
    assert "decide_approvals" not in out["tools"] and "incidents" in out["tools"] and "cleared" in out["kpis"]


def test_the_owner_makes_changes_and_archives_agents_in_the_admin(admin: Any) -> None:  # noqa: F811
    catalogue = admin.get("/admin-api/team/catalogue").json()
    assert {t["key"] for t in catalogue["templates"]} == {t.key for t in templates.offered()} and catalogue["min_job"] == agents.MIN_JOB
    assert "browse" not in {t["key"] for t in catalogue["tools"]} and "agent.create" not in {a["key"] for a in catalogue["actions"]}
    refused = admin.post("/admin-api/team/agents", json={"design": {**_design().model_dump(), "tools": ["run_agent"]}})
    assert refused.status_code == 422 and "Only Nia" in refused.text
    made = admin.post("/admin-api/team/agents", json={"template": "qa_runner", "design": catalogue["templates"][4]["design"] | {"name": "Site Checker"},
                                                      "playbook": "Check the shop first."})
    assert made.status_code == 200, made.text
    detail = made.json()
    assert detail["agent"]["key"] == "site_checker" and detail["agent"]["status"] == "off" and detail["template"] == "qa_runner"
    assert detail["design"]["name"] == "Site Checker" and detail["playbook"]["text"] == "Check the shop first."
    assert admin.get("/admin-api/team/agents/nia").json()["design"] is None, "Nia's design is not the owner's to change"
    assert any(a["key"] == "site_checker" for a in admin.get("/admin-api/team").json()["agents"])
    changed = admin.put("/admin-api/team/agents/site_checker/design", json={**detail["design"], "tools": ["run_checks"], "emits": []})
    assert changed.status_code == 200 and [t["key"] for t in changed.json()["tools"] if t["key"] == "qa_history"] == []
    assert admin.put("/admin-api/team/agents/nia/design", json=detail["design"]).status_code == 404
    assert admin.delete("/admin-api/team/agents/nia").status_code == 404
    gone = admin.delete("/admin-api/team/agents/site_checker").json()
    assert all(a["key"] != "site_checker" for a in gone["agents"])
    log = admin.get("/admin-api/audit").text
    assert "team.agent_created" in log and "team.agent_redesigned" in log and "team.agent_archived" in log
