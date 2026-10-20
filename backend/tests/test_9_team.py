"""The agent team: nothing has a business value in code,
agents wait for the owner's settings, runs stay within the owner's budgets and are costed per
agent, the model only proposes, approvals and earned autonomy, the dispatcher, mailboxes with
an encrypted password, and the admin's Team API.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import (
    AgentTask,
    AIUsage,
    PlatformSetting,
    TeamAgentState,
    TeamApproval,
    TeamEvent,
    TeamPlaybook,
    TeamRun,
)
from app.services import platform
from app.services.team import approvals, config, events, mailboxes, registry, runner, tasks, tools


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


AGENT = "product_analyst"  # a test agent (tests/agents_for_tests.py)
UNKNOWN = "no_such_agent"


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with _db() as db:
        for model in (TeamApproval, TeamRun, TeamEvent, TeamAgentState, TeamPlaybook):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(AgentTask).where(AgentTask.task_type == "team.run"))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("agents", "mailboxes"))))
    yield
    runner.use_model(runner.ProviderModel())


def _ready(**agent: Any) -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, AGENT, config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0, "tools": list(registry.tools_of(registry.AGENTS[AGENT])), **agent}))


class Scripted:
    """A model that makes the given tool calls, one round each, then answers."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], answer: str = "Looked at the numbers; proposed one change.", tokens: int = 1000) -> None:
        self.calls, self.answer, self.tokens = calls, answer, tokens
        self.results: list[dict[str, Any]] = []
        self.system = self.prompt = ""
        self.declared: list[str] = []

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_more: Any) -> str:
        self.system, self.prompt, self.declared = system, prompt, [d["name"] for d in declarations]
        for name, args in self.calls:
            before_round()
            on_usage(runner.Usage("fake-model", self.tokens, self.tokens, 5))
            self.results.append(call_tool(name, args))
        before_round()
        on_usage(runner.Usage("fake-model", self.tokens, self.tokens, 5))
        return self.answer


# --- nothing set in code ---------------------------------------------------------------------------
def test_every_agent_starts_off_without_any_value() -> None:
    assert platform.defaults()["agents"] == {} and platform.defaults()["mailboxes"] == {}
    with _db() as db:
        team = config.team(db)
        assert (team.enabled, team.language, team.ai_daily_usd, team.ai_monthly_usd, team.autonomy_offer_after) == (False, None, None, None, None)
        for key in registry.AGENTS:
            mine = config.agent(db, key)
            assert not mine.enabled and mine.tier is None and mine.ai_daily_usd is None and not mine.tools and not mine.schedule.times and mine.schedule.every_minutes is None
        assert config.status(db, AGENT) == ("off", [])
        assert config.status(db, UNKNOWN)[0] == "unavailable"


def test_the_core_ships_nia_and_no_business_agents() -> None:
    from app.services.team.registry import _AGENTS

    assert [a.key for a in _AGENTS] == ["nia"], "every other agent is the owner's own"
    assert "Leadership" in registry.departments()
    assert not {"scout", "research_lead", "travel_rules_keeper", "showcase_curator", "member_success", "ad_placement", "market_intelligence", "outreach"} & {a.key for a in _AGENTS}
    assert not {"source.add", "travel.rule", "listing.verdict", "members.tip", "partner.check", "ad.plan"} & set(registry.ACTIONS)
    for agent in registry.AGENTS.values():
        assert agent.kpi == "" or agent.kpi in {k.key for k in __import__("app.services.insights.queries", fromlist=["KPIS"]).KPIS}


def test_an_agent_switched_on_waits_for_each_missing_setting() -> None:
    with _db() as db:
        config.save_agent(db, AGENT, config.AgentSettings(enabled=True))
        state, waiting = config.status(db, AGENT)
    assert state == "waiting" and waiting == ["Team switched on", "Team language", "Team AI budget per day", "Model tier", "AI budget per day", "Tools"]
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.status == "waiting" and "Model tier" in outcome.waiting_for
    _ready()
    with _db() as db:
        assert config.status(db, AGENT) == ("ready", [])


def test_settings_the_design_does_not_allow_are_refused() -> None:
    with _db() as db:
        with pytest.raises(ValueError, match="Unknown agent"):
            config.save_agent(db, UNKNOWN, config.AgentSettings(enabled=True))
        with pytest.raises(ValueError, match="cannot use"):
            config.save_agent(db, AGENT, config.AgentSettings(tools=["send_mass_email"]))
        with pytest.raises(ValueError, match="always decided by the owner"):
            config.save_agent(db, AGENT, config.AgentSettings(levels={"playbook.change": "L2"}, l2_daily_limit={"playbook.change": 3}))
        with pytest.raises(ValueError, match="has no action"):
            config.save_agent(db, AGENT, config.AgentSettings(proposals_per_day={"mass_email.send": 3}))
    with pytest.raises(ValueError, match="HH:MM"):
        config.AgentSchedule(times=["9am"])


# --- a run ---------------------------------------------------------------------------------------
def test_a_run_reads_proposes_keeps_a_note_and_is_costed_per_agent() -> None:
    _ready()
    with _db() as db:
        events.emit(db, "approval.decided", "owner", {"agent": "qa_runner", "note": "not for this agent"})
    model = Scripted([
        ("read_insights", {"days": 7}),
        ("keep_note", {"text": "No proposals cleared yet this week.", "evidence": "cleared = 0 (7 days)"}),
        ("propose", {"action_type": "playbook.change", "title": "Look at the sign-up step first", "payload_json": '{"text": "Start with the sign-up step."}',
                     "reason": "Most are lost there", "evidence": ["cleared"]}),
        ("emit_event", {"type": "lead.found", "payload_json": "{}"}),
        ("send_mass_email", {}),
    ])
    runner.use_model(model)
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.status == "done" and outcome.summary.startswith("Looked at")
    kpis = model.results[0]
    assert {k["key"] for k in kpis["kpis"]} >= {"agent_runs", "cleared", "ai_cost"} and "funnels" not in kpis
    assert model.results[2]["status"] == "waiting for the owner's approval"
    assert "error" in model.results[3], "an agent sends only the events its design lists"
    assert "error" in model.results[4], "a tool it was not given is refused"
    assert "Product Analyst" in model.system and "(none yet)" in model.system and "propose; the owner decides" in model.system
    assert "not for this agent" not in model.prompt, "events meant for another agent are not shown"
    with _db() as db:
        run = db.get(TeamRun, outcome.run_id)
        assert run is not None and run.status == "done" and [c["tool"] for c in run.tool_calls][:3] == ["read_insights", "keep_note", "propose"]
        assert run.input_tokens == 6000 and run.cost_usd > 0
        usage = list(db.execute(select(AIUsage).where(AIUsage.operation == f"agent.{AGENT}.run")).scalars())
        assert len(usage) == 6 and all(u.agent_type == "team" for u in usage)
        proposal = db.execute(select(TeamApproval)).scalar_one()
        assert (proposal.level, proposal.status, proposal.run_id) == ("L3", "pending", outcome.run_id)
        assert events.state(db, AGENT).notes[0]["text"].startswith("No proposals")


def test_tools_are_only_those_the_owner_granted() -> None:
    _ready(tools=["read_insights"])
    model = Scripted([("propose", {"action_type": "playbook.change", "title": "x", "payload_json": "{}"})])
    runner.use_model(model)
    runner.run(_db, AGENT, trigger="manual")
    # Only what the owner ticked — and proposal_status, which every agent always has (it only reads what became of its own proposals).
    assert model.declared == ["read_insights", "proposal_status"] and "error" in model.results[0]


def test_budgets_stop_a_run_before_the_next_model_call() -> None:
    _ready(ai_daily_usd=0.0)
    model = Scripted([])
    runner.use_model(model)
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.status == "skipped" and "budget for today" in outcome.summary and model.prompt == "", "no model call at all"
    _ready(ai_daily_usd=0.0001)
    runner.use_model(Scripted([("read_insights", {}), ("read_insights", {})], tokens=200_000))
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.status == "skipped", "the second round is not started once the budget is used"
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0, ai_monthly_usd=0.0))
        config.save_agent(db, AGENT, config.AgentSettings.model_validate({**config.agent(db, AGENT).model_dump(), "ai_daily_usd": 10.0}))
        assert runner.budget_problem(db, AGENT) == "The team's AI budget for this month is used."


# --- approvals ---------------------------------------------------------------------------------------
def test_an_approved_playbook_change_is_carried_out_and_the_agent_learns_from_rejections() -> None:
    _ready()
    with _db() as db:
        first = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="Focus on Ghana", payload={"text": "Ghana first."})
        approvals.decide(db, [first], "approve", admin_id=1, edited_payload={"text": "Ghana and Nigeria first."})
        assert tasks.dispatch(db)["executed"] == 1
        assert db.get(TeamApproval, first.id).status == "executed"  # type: ignore[union-attr]
        second = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="Every country at once", payload={"text": "Everyone."})
        approvals.decide(db, [second], "reject", admin_id=1, note="Too broad: one country first.")
        assert approvals.decide(db, [second], "approve", admin_id=1) == [], "a decided proposal stays decided"
        playbook = approvals.latest_playbook(db, AGENT)
        assert playbook is not None and playbook.text == "Ghana and Nigeria first." and playbook.created_by == f"approval #{first.id}"
    model = Scripted([])
    runner.use_model(model)
    runner.run(_db, AGENT, trigger="manual")
    assert "Ghana and Nigeria first." in model.system
    assert "Too broad: one country first." in model.prompt and "approval.executed" in model.prompt


def test_earned_autonomy_is_offered_at_the_owners_number_and_only_runs_within_the_owners_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry.ACTIONS, "test.ping", registry.ActionType("test.ping", "Ping", "L2"))
    monkeypatch.setattr(registry, "COMMON_ACTIONS", ("playbook.change", "test.ping"))
    monkeypatch.setitem(approvals.EXECUTORS, "test.ping", lambda _s, _a, payload: {"pong": payload.get("n")})
    _ready()
    with _db() as db:
        config.save_team(db, config.TeamSettings.model_validate({**config.team(db).model_dump(), "autonomy_offer_after": 2}))
        rows = [approvals.propose(db, agent=AGENT, action_type="test.ping", title=f"ping {n}", payload={"n": n}) for n in range(3)]
        approvals.decide(db, [rows[0]], "approve", admin_id=1, edited_payload={"n": 99})
        approvals.decide(db, [rows[1]], "approve", admin_id=1)
        assert events.state(db, AGENT).autonomy_offers == [], "an edit resets the count"
        approvals.decide(db, [rows[2]], "approve", admin_id=1)
        assert approvals.streak(db, AGENT, "test.ping") == 2 and events.state(db, AGENT).autonomy_offers == ["test.ping"]
        assert config.level_of(config.agent(db, AGENT), "test.ping") == "L1", "an offer changes nothing by itself"
        with pytest.raises(ValueError, match="per day"):
            approvals.answer_offer(db, AGENT, "test.ping", accept=True, daily_limit=None)
        approvals.answer_offer(db, AGENT, "test.ping", accept=True, daily_limit=1)
        assert config.level_of(config.agent(db, AGENT), "test.ping") == "L2" and events.state(db, AGENT).autonomy_offers == []
        alone = approvals.propose(db, agent=AGENT, action_type="test.ping", title="alone", payload={"n": 1})
        over = approvals.propose(db, agent=AGENT, action_type="test.ping", title="over the limit", payload={"n": 2})
        assert (alone.status, over.status) == ("approved", "pending")
        tasks.dispatch(db)
        assert alone.status == "executed" and alone.result == {"pong": 1}


def test_an_action_that_fails_in_the_database_is_recorded_and_the_next_one_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import text

    def broken(session: Session, _approval: TeamApproval, _payload: dict[str, Any]) -> dict[str, Any]:
        session.execute(text("SELECT * FROM no_such_table_here"))
        return {}

    monkeypatch.setitem(approvals.EXECUTORS, "product.idea", broken)
    _ready()
    with _db() as db:
        bad = approvals.propose(db, agent=AGENT, action_type="product.idea", title="breaks", payload={"title": "breaks", "idea": "Show the deadline first.", "metric": "agent_runs"})
        good = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="works", payload={"text": "Fine."})
        approvals.decide(db, [bad, good], "approve", admin_id=1)
        assert approvals.execute_due(db) == 2
        bad_id, good_id = bad.id, good.id
    with _db() as db:
        failed, done = db.get(TeamApproval, bad_id), db.get(TeamApproval, good_id)
        assert failed is not None and failed.status == "failed" and "no_such_table_here" in failed.result["error"]
        assert done is not None and done.status == "executed" and approvals.latest_playbook(db, AGENT) is not None


def test_proposals_per_day_cap() -> None:
    _ready(proposals_per_day={"product.idea": 1})
    with _db() as db:
        approvals.propose(db, agent=AGENT, action_type="product.idea", title="one", payload={"title": "one", "idea": "Show the deadline first.", "metric": "agent_runs"})
        with pytest.raises(approvals.ProposalRefused, match="limit"):
            approvals.propose(db, agent=AGENT, action_type="product.idea", title="two", payload={"title": "two", "idea": "Show the deadline first.", "metric": "agent_runs"})
        with pytest.raises(approvals.ProposalRefused):
            approvals.propose(db, agent=UNKNOWN, action_type="mass_email.send", title="x", payload={})


# --- when agents run --------------------------------------------------------------------------------
def test_the_schedule_is_the_owners_and_counts_from_when_the_agent_is_ready() -> None:
    at = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)  # Monday 09:30 in the owner's time zone (UTC in tests)
    every = config.AgentSchedule(every_minutes=30)
    assert runner.due(every, None, at) and not runner.due(every, at - timedelta(minutes=10), at) and runner.due(every, at - timedelta(minutes=30), at)
    morning = config.AgentSchedule(times=["09:00"], weekdays=[0])
    assert runner.due(morning, at - timedelta(hours=1), at) and not runner.due(morning, at - timedelta(minutes=20), at)
    assert not runner.due(config.AgentSchedule(times=["09:00"], weekdays=[1]), at - timedelta(days=1), at)
    assert not runner.due(config.AgentSchedule(), None, at), "no schedule: only events and the owner start it"
    weekday_minutes = config.AgentSchedule(every_minutes=30, weekdays=[1])
    assert not runner.due(weekday_minutes, None, at), "ticked days apply to 'every … minutes' too"
    assert runner.due(config.AgentSchedule(every_minutes=30, weekdays=[0]), None, at)


def test_days_without_a_time_are_refused_and_the_schedule_reads_as_a_sentence() -> None:
    with pytest.raises(ValueError, match="never start"):
        config.check(AGENT, config.AgentSettings(schedule=config.AgentSchedule(weekdays=[0, 2])))
    config.check(AGENT, config.AgentSettings(schedule=config.AgentSchedule(weekdays=[0], times=["08:00"])))
    assert config.schedule_text(config.AgentSchedule()).startswith("No schedule")
    assert config.schedule_text(config.AgentSchedule(times=["08:00"], weekdays=[0, 2])) == "At 08:00, Mon, Wed (your time zone)."
    assert config.schedule_text(config.AgentSchedule(every_minutes=60)) == "Every 60 min, every day (your time zone)."


def test_an_agent_without_results_pauses_at_the_owners_cost_per_result() -> None:
    _ready(max_cost_per_result_usd=0.01)
    with _db() as db:
        assert runner.budget_problem(db, AGENT) == ""
    runner.use_model(Scripted([("read_insights", {})], tokens=200_000))
    runner.run(_db, AGENT, trigger="manual")
    with _db() as db:
        assert runner.budget_problem(db, AGENT).startswith("Paused for today"), "it spent a result's worth without a result"
        approvals.propose(db, agent=AGENT, action_type="playbook.change", title="x", payload={"text": "y"})
    with _db() as db:
        spent = runner.spent(db, f"agent.{AGENT}.run", datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
        assert (runner.budget_problem(db, AGENT) == "") == (spent < 0.02), "each result allows one more result's worth"


def test_the_same_proposal_is_not_made_twice_while_the_first_waits() -> None:
    _ready()
    with _db() as db:
        first = approvals.propose(db, agent=AGENT, action_type="product.idea", title="Alpha", payload={"title": "Alpha", "idea": "Show the deadline first.", "metric": "agent_runs"})
        approvals.propose(db, agent=AGENT, action_type="product.idea", title="Beta", payload={"title": "Beta", "idea": "Show the deadline first.", "metric": "agent_runs"})
        with pytest.raises(approvals.ProposalRefused, match=f"#{first.id}"):
            approvals.propose(db, agent=AGENT, action_type="product.idea", title="Alpha again", payload={"title": "Alpha", "idea": "Show the deadline first.", "metric": "agent_runs"})
        approvals.decide(db, [first], "reject", admin_id=1)
        approvals.propose(db, agent=AGENT, action_type="product.idea", title="Alpha, better", payload={"title": "Alpha", "idea": "Show the deadline first.", "metric": "agent_runs"})


def test_reasoning_written_as_text_is_not_the_summary() -> None:
    _ready()
    runner.use_model(Scripted([("read_insights", {})], answer="thought\nLet's see: the owner wants..."))
    assert runner.run(_db, AGENT, trigger="manual").summary.startswith("The agent wrote no summary")


def test_a_run_without_a_summary_is_described_from_its_record() -> None:
    _ready()
    runner.use_model(Scripted([("read_insights", {}), ("propose", {"action_type": "playbook.change", "title": "x", "payload_json": '{"text": "y"}'})], answer=""))
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.summary.startswith("The agent wrote no summary") and "read_insights ×1" in outcome.summary and "Proposals: #" in outcome.summary


def test_the_dispatcher_starts_ready_agents_once_and_does_nothing_while_the_team_is_off() -> None:
    _ready(schedule={"every_minutes": 60})
    now = datetime.now(UTC)
    with _db() as db:
        assert tasks.dispatch(db, now)["started"] == [], "the first tick only starts the schedule's clock"
        events.emit(db, "approval.decided", "owner", {"agent": AGENT})
        assert tasks.dispatch(db, now + timedelta(minutes=1))["started"] == [AGENT]
        assert tasks.dispatch(db, now + timedelta(minutes=2))["started"] == [], "one queued run per agent"
        queued = db.execute(select(AgentTask).where(AgentTask.task_type == "team.run")).scalars().all()
        assert len(queued) == 1 and queued[0].payload == {"agent": AGENT, "trigger": "event"}
        config.save_team(db, config.TeamSettings.model_validate({**config.team(db).model_dump(), "enabled": False}))
        assert tasks.dispatch(db, now + timedelta(hours=2)) == {"team": "off"}


# --- mailboxes ---------------------------------------------------------------------------------------
def test_the_mailbox_password_is_stored_encrypted_and_never_shown() -> None:
    with _db() as db:
        empty = mailboxes.view(db, "marketing")
        assert not empty.password_set and "Password" in empty.missing and "SMTP server" in empty.missing
        assert mailboxes.test(db, "marketing")["smtp"].startswith("Not set")
        box = mailboxes.Mailbox(address="post@tower.example", login="post@tower.example", smtp_host="smtp.example", smtp_port=465,
                                smtp_security="ssl", imap_host="imap.example", imap_port=993)
        view = mailboxes.save(db, "marketing", box, password="very-secret-mail-password")  # noqa: S106 - a throwaway test value
        assert view.password_set and view.missing == [] and "very-secret" not in view.model_dump_json()
        stored = str(platform.get(db, "mailboxes"))
        assert "very-secret" not in stored and "password_enc" in stored
        mailboxes.save(db, "marketing", box, password=None)
        assert mailboxes.secret(db, "marketing") == "very-secret-mail-password", "no password given keeps the stored one"
        cfg = mailboxes.smtp_config(db, "marketing")
        assert cfg is not None and not cfg.use_tls
        assert cfg.password == "very-secret-mail-password"  # noqa: S105 - a throwaway test value
        assert not mailboxes.save(db, "marketing", box, password="").password_set
        with pytest.raises(ValueError):
            mailboxes.save(db, "sales", box, password=None)


def test_no_team_table_holds_a_person() -> None:
    from app.models import team as models

    for model in (models.TeamRun, models.TeamEvent, models.TeamApproval, models.TeamAgentState, models.TeamPlaybook):
        assert not set(model.__table__.columns.keys()) & {"user_id", "employer_id", "member_id", "email", "ip_address"}, model


# --- the admin's Team API -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin() -> Iterator[Any]:
    from contextlib import redirect_stdout
    from io import StringIO
    from urllib.parse import parse_qs, urlparse

    from app.admin_cli import main as admin_cli
    from app.main import app as admin_app
    from tests.test_9_admin_dashboard import AdminClient, _fresh_code

    email = f"team-owner-{uuid4().hex[:6]}@tower.example"
    buffer = StringIO()
    with redirect_stdout(buffer):
        admin_cli(["create-owner", "--email", email, "--name", "Tara Team"])
    link = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if "accept-invite?token=" in line)
    with AdminClient(admin_app) as c:
        c.post("/admin-api/auth/accept-invite", json={"token": parse_qs(urlparse(link).query)["token"][0], "password": "team-owner-passphrase-2026"})
        r = c.post("/admin-api/auth/login", json={"email": email, "password": "team-owner-passphrase-2026"})
        c.csrf = r.json()["csrf_token"]
        secret = c.post("/admin-api/auth/enroll/start").json()["secret"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(secret, email)}).status_code == 200
        yield c


def test_the_owner_runs_the_team_from_the_admin_app(admin: Any) -> None:
    overview = admin.get("/admin-api/team").json()
    assert len(overview["agents"]) == len(registry.AGENTS) and overview["team"]["enabled"] is False and overview["pending_approvals"] == 0
    refused = admin.put(f"/admin-api/team/agents/{UNKNOWN}", json={"enabled": True})
    assert refused.status_code in (400, 404, 422)
    assert admin.post(f"/admin-api/team/agents/{AGENT}/run").status_code in (400, 422), "an agent that is off is not started"
    saved = admin.put(f"/admin-api/team/agents/{AGENT}", json={"enabled": True, "tier": "fast", "ai_daily_usd": 1, "tools": ["read_insights"]}).json()
    assert saved["agent"]["status"] == "waiting" and saved["agent"]["waiting_for"] == ["Team switched on", "Team language", "Team AI budget per day"]
    admin.put("/admin-api/team/settings", json={"enabled": True, "language": "en", "ai_daily_usd": 3})
    assert admin.post(f"/admin-api/team/agents/{AGENT}/run").json() == {"queued": True}
    detail = admin.put(f"/admin-api/team/agents/{AGENT}/playbook", json={"text": "Ghana first.", "note": "start"}).json()
    assert detail["playbook"]["version"] == 1 and detail["actions"][0]["key"] == "playbook.change"
    with _db() as db:
        proposal_id = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="Add Nigeria", payload={"text": "Ghana and Nigeria."}).id
    inbox = admin.get("/admin-api/team/approvals").json()
    assert [i["id"] for i in inbox["items"]] == [proposal_id] and inbox["items"][0]["action_label"] == "Change the agent's own playbook"
    decided = admin.post("/admin-api/team/approvals/decide", json={"ids": [proposal_id], "decision": "reject", "note": "Not yet."}).json()
    assert decided["items"][0]["status"] == "rejected" and decided["counts"]["rejected"] == 1
    box = admin.put("/admin-api/team/mailboxes/marketing", json={"address": "post@tower.example", "login": "post@tower.example", "smtp_host": "smtp.example",
                                                                  "smtp_port": 465, "smtp_security": "ssl", "imap_host": "imap.example", "imap_port": 993, "password": "pw-from-the-owner"})
    assert box.status_code == 200 and box.json()["password_set"] and "pw-from-the-owner" not in box.text
    listed = admin.get("/admin-api/team/mailboxes")
    assert "pw-from-the-owner" not in listed.text and listed.json()[0]["missing"] == []
    log = admin.get("/admin-api/audit").text
    assert "team.mailbox_changed" in log and "pw-from-the-owner" not in log


def test_every_reading_tool_answers_outside_its_session() -> None:
    """Results are built while the session is open (a closed session's rows cannot be read)."""
    _ready()
    with _db() as db:
        events.emit(db, "alert.opened", "owner", {"rule": "test"})
    agent = registry.AGENTS["nia"]
    ctx = tools.Context(agent=agent, run_id=0, db=_db, granted=tuple(tools.CATALOGUE))
    for name, args in (("read_events", {}), ("read_insights", {"days": 7}), ("team_overview", {}), ("approvals_waiting", {}),
                       ("read_memories", {}), ("social_overview", {}), ("ops_signals", {}), ("incidents", {}), ("qa_history", {}),
                       ("product_numbers", {"days": 7}), ("health_numbers", {})):
        result = tools.run(ctx, name, args)
        assert "error" not in result, (name, result)
    assert tools.run(ctx, "read_events", {})["events"][0]["type"] == "alert.opened"


def test_a_newer_playbook_change_replaces_the_one_still_waiting() -> None:
    _ready()
    with _db() as db:
        first = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="First", payload={"text": "a"})
        second = approvals.propose(db, agent=AGENT, action_type="playbook.change", title="Second", payload={"text": "a and b"})
        assert (first.status, second.status) == ("withdrawn", "pending") and "Replaced" in first.decision_note
        approvals.decide(db, [second], "approve", admin_id=1)
        with pytest.raises(approvals.ProposalRefused, match="Already waiting"):
            approvals.propose(db, agent=AGENT, action_type="playbook.change", title="Third", payload={"text": "c"})


def test_an_agent_is_told_before_its_last_round_and_some_agents_get_more_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai import types

    from app.config import settings

    requests: list[tuple[list[Any], Any]] = []

    class Models:
        def generate_content(self, *, model: str, contents: list[Any], config: Any) -> types.GenerateContentResponse:
            requests.append((list(contents), config))
            # Research every round; in the last round (only recording allowed) it proposes.
            fc = config.tool_config.function_calling_config if config.tool_config else None
            name = "propose" if fc is not None and fc.allowed_function_names else "read_insights"
            args = {"action_type": "playbook.change", "title": "t", "payload_json": "{}"} if name == "propose" else {}
            call = types.Part(function_call=types.FunctionCall(id=f"c{len(requests)}", name=name, args=args))
            return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[call]))],
                                                 usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5))

    class Client:
        def __init__(self, api_key: str, http_options: Any) -> None:
            self.models = Models()

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

    monkeypatch.setattr("google.genai.Client", Client)
    monkeypatch.setattr(settings(), "gemini_api_key", "test-key")
    calls: list[str] = []
    runner.ProviderModel().converse(tier="fast", system="s", prompt="p", declarations=tools.declarations(("read_insights", "propose")),
                                  call_tool=lambda name, args: calls.append(name) or {"ok": True}, before_round=lambda: None, on_usage=lambda u: None, max_rounds=3)
    assert calls == ["read_insights", "read_insights", "read_insights", "propose"], "three rounds of research, and the last round's proposal is carried out"
    assert len(requests) == 5, "four rounds, then the summary request"
    warned = [i for i, (contents, _c) in enumerate(requests) if any(p.text == runner.LAST_ROUND for c in contents for p in (c.parts or []))]
    assert warned and warned[0] == 3, "the warning comes with the results before the last round"
    last = requests[3][1].tool_config.function_calling_config
    assert last.mode == types.FunctionCallingConfigMode.ANY and last.allowed_function_names == ["propose"], "the last round may only record (Gemini needs ANY for a list)"
    assert requests[4][1].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE, "the summary request calls no tool"
    assert registry.AGENTS["marketing_lead"].max_rounds == 16 and runner.rounds_of(registry.AGENTS["qa_runner"]) == runner.MAX_ROUNDS
    # Nia reads before she proposes (agent_profile, read_settings, then the change), so she has more rounds.
    assert runner.rounds_of(registry.AGENTS["nia"]) == 16
    team = config.TeamSettings(enabled=True, language="en")
    assert "at most 16 rounds" in runner.system_prompt(registry.AGENTS["marketing_lead"], config.AgentSettings(), team, "")
    assert f"at most {runner.MAX_ROUNDS} rounds" in runner.system_prompt(registry.AGENTS["qa_runner"], config.AgentSettings(), team, "")


def test_deciding_many_proposals_at_once_is_logged_whole(admin: Any) -> None:
    from app.admin.auth import record
    from app.db import admin_transaction
    from app.models import AdminAuditLog

    with admin_transaction(write=True) as db:
        record(db, None, "team.approval_approved", target_type="team_approval", target_id=",".join(str(i) for i in range(1000, 1200))[:200])
        row = db.execute(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(1)).scalar_one()
        assert len(row.target_id) <= 100 and row.metadata_["targets"].startswith("1000,1001")


def test_a_stale_card_in_a_batch_is_withdrawn_and_the_rest_approved(admin: Any) -> None:
    from app.services.team import approvals as appr

    with _db() as db:
        good = appr.propose(db, agent=AGENT, action_type="playbook.change", title="Good", payload={"text": "Do good work.", "note": "x"})
        good_id = good.id
    # A recheck that finds the other card outdated (as when its listing was published meanwhile).
    with _db() as db:
        stale = appr.propose(db, agent="qa_runner", action_type="playbook.change", title="Stale", payload={"text": "Outdated.", "note": "y"})
        stale_id = stale.id
    original = appr.RECHECKS.get("playbook.change")
    appr.RECHECKS["playbook.change"] = lambda _s, row: ["no longer applies."] if row.id == stale_id else []
    try:
        r = admin.post("/admin-api/team/approvals/decide", json={"ids": [good_id, stale_id], "decision": "approve"})
    finally:
        if original is None:
            appr.RECHECKS.pop("playbook.change", None)
        else:
            appr.RECHECKS["playbook.change"] = original
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["id"] for i in body["items"]] == [good_id] and body["withdrawn"] and str(stale_id) in body["withdrawn"][0]
