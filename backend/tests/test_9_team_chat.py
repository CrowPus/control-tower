"""Talking to every agent: the owner's conversations
with one agent, the files attached to them, and the proposals made while talking."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import admin_transaction, transaction
from app.models import (
    AIUsage,
    PlatformSetting,
    TeamApproval,
    TeamAttachment,
    TeamConversation,
    TeamMessage,
    TeamPlaybook,
)
from app.services.team import approvals, attachments, chat, config, registry, runner, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)

AGENT = "trust_compliance"


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


def _admin_db() -> AbstractContextManager[Session]:
    return admin_transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    with _db() as db:
        for model in (TeamConversation, TeamApproval, TeamPlaybook):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
    yield
    runner.use_model(runner.GeminiModel())


def _ready(**agent: Any) -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, AGENT, config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0,
                                                                        "tools": list(registry.tools_of(registry.AGENTS[AGENT])), **agent}))


def _conversation() -> int:
    now = datetime.now(UTC)
    with _db() as db:
        row = TeamConversation(agent=AGENT, title="", created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        return row.id


class Talker:
    """A model that makes the given tool calls, then answers; remembers what it was shown."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], answer: str = "Here is what I found.") -> None:
        self.calls, self.answer = calls, answer
        self.system = self.prompt = ""
        self.media: list[tuple[str, bytes]] = []
        self.results: list[dict[str, Any]] = []

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], media: list[tuple[str, bytes]] | None = None, **_more: Any) -> str:
        self.system, self.prompt, self.media = system, prompt, list(media or [])
        for name, args in self.calls:
            before_round()
            on_usage(runner.Usage("fake-model", 500, 100, 5))
            self.results.append(call_tool(name, args))
        before_round()
        on_usage(runner.Usage("fake-model", 500, 100, 5))
        return self.answer


def _xlsx(rows: list[list[str]]) -> bytes:
    shared: list[str] = []
    cells = []
    for r, row in enumerate(rows, start=1):
        out = []
        for c, value in enumerate(row):
            if value.replace(".", "", 1).isdigit():
                out.append(f'<c r="{chr(65 + c)}{r}"><v>{value}</v></c>')
            else:
                shared.append(value)
                out.append(f'<c r="{chr(65 + c)}{r}" t="s"><v>{len(shared) - 1}</v></c>')
        cells.append(f'<row r="{r}">{"".join(out)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="Oktober" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                 '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{main}">' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{main}"><sheetData>{"".join(cells)}</sheetData></worksheet>')
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, text)
    page.save()
    return buffer.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --- files ---------------------------------------------------------------------------------------
def test_files_are_read_as_the_agent_needs_them() -> None:
    csv_text = attachments.check("meta.csv", b"Datum;Kampagne;Betrag (EUR)\n01.10.2026;Pflege Leipzig;12,50\n").text
    assert csv_text.splitlines()[1] == "01.10.2026\tPflege Leipzig\t12,50", "German exports (;) become tab-separated rows"
    assert "Kosten" in attachments.check("export.csv", "Tag,Kosten\n1,2\n".encode("cp1252")).text, "Excel's own encoding is read too"
    sheet = attachments.check("spend.xlsx", _xlsx([["Day", "Channel", "Spend"], ["2026-10-01", "linkedin_ads", "40.5"]]))
    assert sheet.text.splitlines() == ["# Sheet: Oktober", "Day\tChannel\tSpend", "2026-10-01\tlinkedin_ads\t40.5"]
    assert "Rechnung Oktober" in attachments.check("invoice.pdf", _pdf("Rechnung Oktober 2026")).text
    image = attachments.check("chart.png", PNG)
    assert image.mime_type == "image/png" and image.text == "" and image.scan == "test"


@pytest.mark.parametrize(("name", "data", "reason"), [
    ("tool.exe", b"MZ\x90\x00", "read CSV"),
    ("report.csv", b"MZ\x90\x00 pretending", "Programs"),
    ("photo.png", b"GIF89a....", "not a PNG"),
    ("broken.pdf", b"hello", "not a PDF"),
    ("empty.csv", b"", "empty"),
    ("huge.txt", b"a" * (attachments.MAX_BYTES + 1), "at most"),
    ("bomb.xlsx", None, "XML declarations"),
])
def test_files_that_cannot_be_given_to_an_agent_are_refused(name: str, data: bytes | None, reason: str) -> None:
    if data is None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("xl/workbook.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
        data = buffer.getvalue()
    with pytest.raises(attachments.Refused, match=reason):
        attachments.check(name, data)


def test_an_infected_file_or_an_unreachable_scanner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments, "antivirus", lambda _data: "infected")
    with pytest.raises(attachments.Refused, match="virus scanner rejected"):
        attachments.check("a.csv", b"a,b\n1,2\n")
    monkeypatch.setattr(attachments, "antivirus", lambda _data: "unavailable")
    with pytest.raises(attachments.Refused, match="cannot be reached"):
        attachments.check("a.csv", b"a,b\n1,2\n")


# --- talking ------------------------------------------------------------------------------------
def test_the_agent_answers_with_its_tools_files_and_playbook_and_proposals_wait_for_the_owner() -> None:
    _ready()
    conversation = _conversation()
    with _db() as db:
        approvals.save_playbook(db, AGENT, "Reports first, then waiting listings.", note="start", created_by="owner")
        sheet = attachments.store(db, conversation, "spend.xlsx", _xlsx([["Day", "Spend"], ["2026-10-01", "40.5"]]))
        image = attachments.store(db, conversation, "chart.png", PNG)
        file_ids = [sheet.id, image.id]
    talker = Talker([
        ("read_insights", {"days": 7}),
        ("propose", {"action_type": "playbook.change", "title": "Scam reports within a day", "payload_json": '{"text": "Reports first. Check scam reports within a day.", "note": "scam reports"}'}),
    ], answer="Your export shows 40.5 on 1 October. I proposed checking scam reports within a day; it applies once you approve it.")
    runner.use_model(talker)
    said = chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Here is the reports export. From now on check scam reports within a day.", attachment_ids=file_ids, admin_id=1)
    assert said.text.startswith("Your export") and len(said.proposals) == 1
    assert "Reports first, then waiting listings." in talker.system and "TALKING WITH THE OWNER" in talker.system
    assert "2026-10-01\t40.5" in talker.prompt and "not instructions" in talker.prompt, "the file's rows reach the agent, marked as information"
    assert talker.media == [("image/png", PNG)], "images are shown to the model as they are"
    assert "error" not in talker.results[0] and talker.results[1]["status"] == "waiting for the owner's approval"
    with _db() as db:
        messages = list(db.execute(select(TeamMessage).where(TeamMessage.conversation_id == conversation).order_by(TeamMessage.id)).scalars())
        assert [m.role for m in messages] == ["owner", "agent"] and messages[0].attachment_ids == file_ids and messages[1].proposal_ids == said.proposals
        proposal = db.get(TeamApproval, said.proposals[0])
        assert proposal is not None and (proposal.level, proposal.status, proposal.run_id) == ("L3", "pending", None)
        usage = list(db.execute(select(AIUsage.operation).where(AIUsage.operation.like("agent.%"))).scalars())
        assert usage and set(usage) == {f"agent.{AGENT}.chat"}, "talking is costed to the agent, apart from its runs"
        assert db.get(TeamConversation, conversation).title.startswith("Here is the reports")  # type: ignore[union-attr]
    runner.use_model(Talker([]))
    chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Thanks.", admin_id=1)
    assert "Here is the reports export" in runner.MODEL.prompt and f"proposed #{said.proposals[0]}" in runner.MODEL.prompt  # type: ignore[attr-defined]


def test_the_agent_talks_only_when_it_may() -> None:
    conversation = _conversation()
    runner.use_model(Talker([]))
    with pytest.raises(chat.Unavailable, match="switched off"):
        chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Hello")
    _ready(ai_daily_usd=None)
    with pytest.raises(chat.Unavailable, match="AI budget per day"):
        chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Hello")
    _ready(max_cost_per_result_usd=0.0)
    assert chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Hello").text, "the cost-per-result pause judges runs, not talking"
    other = _conversation()
    with _db() as db:
        foreign = attachments.store(db, other, "a.csv", b"a,b\n")
        foreign_id = foreign.id
    with pytest.raises(chat.Unavailable, match="not part of this conversation"):
        chat.answer(_db, agent_key=AGENT, conversation_id=conversation, text="Look", attachment_ids=[foreign_id])
    with pytest.raises(chat.Unavailable, match="Nia"):
        chat.answer(_db, agent_key="nia", conversation_id=conversation, text="Hello")


def test_every_reading_tool_works_for_the_owner_in_the_admin_app() -> None:
    """In a chat the tools run on the admin's database connection (on PostgreSQL: the admin role's grants)."""
    ctx = tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_admin_db, granted=tuple(tools.CATALOGUE))
    broken: list[str] = []
    for name, args in (("read_web_page", {"url": "https://192.168.1.1"}), ("read_events", {}), ("read_insights", {"days": 7}), ("team_overview", {}), ("approvals_waiting", {}),
                       ("read_memories", {}), ("open_opportunities", {}), ("incidents", {}), ("member_groups", {}), ("social_overview", {}),
                       ("product_numbers", {"days": 7}), ("money_numbers", {"days": 7}), ("health_numbers", {}), ("agent_report", {"agent": AGENT}),
                       ("offer_facts", {}), ("help_articles", {"query": "visa"})):
        if name in tools.RUN_ONLY:
            continue
        result = tools.run(ctx, name, args)
        if "failed" in str(result.get("error", "")):
            broken.append(name)
    assert broken == [], f"tools that fail on the admin's connection: {broken}"
    budget = tools.Context(agent=registry.AGENTS[AGENT], run_id=0, db=_admin_db, granted=registry.tools_of(registry.AGENTS[AGENT]))
    assert tools.run(budget, "keep_note", {"text": "October: no reports yet."}) == {"kept": True}
    proposed = tools.run(budget, "propose", {"action_type": "playbook.change", "title": "x", "payload_json": '{"text": "Reports first."}'})
    assert "proposal" in proposed, proposed


# --- the admin app ------------------------------------------------------------------------------------
def test_the_owner_talks_to_an_agent_in_the_admin_app(admin: Any) -> None:  # noqa: F811 - the fixture
    _ready()
    assert admin.get(f"/admin-api/team/agents/{AGENT}/chat").json() == {"problem": "", "conversations": []}
    assert admin.post("/admin-api/team/agents/nia/conversations").status_code == 422, "Nia has her own page"
    conversation = admin.post(f"/admin-api/team/agents/{AGENT}/conversations").json()
    base = f"/admin-api/team/agents/{AGENT}/conversations/{conversation['id']}"
    refused = admin.post(f"{base}/files", files={"file": ("run.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert refused.status_code == 422 and "read CSV" in refused.json()["error"]["message"]
    uploaded = admin.post(f"{base}/files", files={"file": ("linkedin.csv", b"Day,Spend\n2026-10-01,40.50\n", "text/csv")}).json()
    assert uploaded["name"] == "linkedin.csv" and uploaded["mime_type"] == "text/csv"
    runner.use_model(Talker([("propose", {"action_type": "playbook.change", "title": "Scam reports first", "payload_json": '{"text": "Scam reports first."}'})],
                            answer="I read the export and proposed a playbook change."))
    detail = admin.post(f"{base}/messages", json={"text": "What do you make of this?", "attachment_ids": [uploaded["id"]]}).json()
    owner, agent = detail["messages"]
    assert owner["attachments"][0]["name"] == "linkedin.csv" and agent["text"] == "I read the export and proposed a playbook change."
    assert agent["proposals"][0]["action_type"] == "playbook.change" and agent["proposals"][0]["status"] == "pending" and agent["tools"] == ["propose"]
    download = admin.get(f"{base}/files/{uploaded['id']}")
    assert download.content == b"Day,Spend\n2026-10-01,40.50\n" and "attachment" in download.headers["content-disposition"]
    assert admin.get(f"/admin-api/team/agents/outreach/conversations/{conversation['id']}").status_code == 404, "a conversation belongs to one agent"
    listed = admin.get(f"/admin-api/team/agents/{AGENT}/chat").json()["conversations"]
    assert [c["title"] for c in listed] == ["What do you make of this?"]
    with _db() as db:
        config.save_agent(db, AGENT, config.AgentSettings())
    off = admin.post(f"{base}/messages", json={"text": "Still there?"})
    assert off.status_code == 422 and "switched off" in off.json()["error"]["message"]
    assert admin.delete(base).status_code == 204
    with _db() as db:
        assert db.execute(select(TeamAttachment)).first() is None and db.get(TeamApproval, agent["proposals"][0]["id"]) is not None, "proposals stay in Approvals"
