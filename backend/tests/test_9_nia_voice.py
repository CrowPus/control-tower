"""Talking to Nia by voice (agent team, stage A2b): only with the owner's voice settings and a
one-time lease from a signed-in request; the words are kept in the conversation, minutes are
costed and capped; Nia may decide L1/L2 proposals the owner names, never L3."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from google.genai import types
from sqlalchemy import delete, select
from starlette.websockets import WebSocketDisconnect

from app.db import transaction
from app.models import AIUsage, NiaConversation, NiaMessage, PlatformSetting, TeamApproval
from app.services.nia import voice
from app.services.team import approvals, config, registry, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    with _db() as db:
        for model in (NiaConversation, TeamApproval):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("agents", "nia"))))
    yield
    voice.CONNECT = None


def _ready(voice_name: str | None = "Kore") -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="de", ai_daily_usd=5.0))
        config.save_agent(db, "nia", config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0, "tools": list(registry.tools_of(registry.AGENTS["nia"]))}))
        voice.save(db, voice.VoiceSettings(voice=voice_name))


class FakeLive:
    """Gemini Live, scripted: a tool call, then Nia's spoken answer with its transcript."""

    def __init__(self, approval_id: int) -> None:
        self.approval_id = approval_id
        self.sent: list[Any] = []
        self.tool_answers: list[Any] = []

    async def send_client_content(self, **kw: Any) -> None:
        self.sent.append(kw)

    async def send_realtime_input(self, **kw: Any) -> None:
        self.sent.append(kw)

    async def send_tool_response(self, **kw: Any) -> None:
        self.tool_answers.append(kw)

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(server_content=types.LiveServerContent(input_transcription=types.Transcription(text="Genehmige den Brief-Stapel.")))
        yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id="1", name="decide_approvals", args={"ids": [self.approval_id], "decision": "approve"})]))
        yield types.LiveServerMessage(server_content=types.LiveServerContent(output_transcription=types.Transcription(text="Erledigt, der Stapel ist freigegeben."),
                                                                             model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b"\x00\x00" * 100, mime_type="audio/pcm"))]),
                                                                             turn_complete=True))
        await __import__("asyncio").sleep(30)


def test_voice_waits_for_the_owners_settings() -> None:
    with _db() as db:
        assert "switched off" in voice.problem(db)
    _ready(voice_name=None)
    with _db() as db:
        assert voice.problem(db) == "Set Nia's voice (Admin → Nia → Voice)."
        voice.save(db, voice.VoiceSettings(voice="Kore"))
        assert voice.problem(db) == "", "no daily minute limit for the owner's own Nia"
        with pytest.raises(ValueError):
            voice.save(db, voice.VoiceSettings(voice="Darth"))
    assert voice.take("not-a-lease") is None
    token = voice.lease(1, 2)
    assert voice.take(token) is not None and voice.take(token) is None, "a lease works once"


def test_nia_never_decides_l3_and_decides_what_the_owner_named() -> None:
    _ready()
    with _db() as db:
        small = approvals.propose(db, agent="product_analyst", action_type="product.idea", title="Deadlines first",
                                  payload={"title": "Deadlines first", "idea": "Show the deadline first.", "metric": "agent_runs"})
        money = approvals.propose(db, agent="marketing_lead", action_type="marketing.plan", title="Plan", payload={"title": "Plan"})
        small_id, money_id = small.id, money.id
    ctx = tools.Context(agent=registry.AGENTS["nia"], run_id=0, db=_db, granted=("decide_approvals",), admin_id=None)
    assert "Only the owner" in tools.run(ctx, "decide_approvals", {"ids": [small_id], "decision": "approve"})["error"]
    ctx.admin_id = 1
    assert "on screen" in tools.run(ctx, "decide_approvals", {"ids": [money_id], "decision": "approve"})["error"]
    assert tools.run(ctx, "decide_approvals", {"ids": [small_id], "decision": "approve"})["decided"] == [small_id]


def test_a_voice_conversation_is_kept_and_costed(admin: Any) -> None:  # noqa: F811 - the fixture
    _ready()
    with _db() as db:
        # A waiting L1 proposal, made directly (its own checks are not what this test is about).
        row = TeamApproval(agent="content_community", action_type="social.post", level="L1", title="Post 1", payload={"networks": ["instagram"], "text": "x"},
                           evidence=[], result={}, status="pending", created_at=datetime.now(UTC))
        db.add(row)
        db.flush()
        proposal_id = row.id
    fake = FakeLive(proposal_id)

    @contextlib.asynccontextmanager
    async def connect(**_kw: Any) -> AsyncIterator[FakeLive]:
        yield fake

    voice.CONNECT = connect
    conversation = admin.post("/admin-api/nia/conversations").json()
    settings_view = admin.get("/admin-api/nia/voice").json()
    assert settings_view["problem"] == "" and settings_view["voice"] == "Kore" and "Kore" in settings_view["voices"]
    lease = admin.post("/admin-api/nia/voice/lease", json={"conversation_id": conversation["id"]}).json()
    with pytest.raises(WebSocketDisconnect), admin.websocket_connect(f"{lease['path']}?lease=nope"):
        pass
    with admin.websocket_connect(f"{lease['path']}?lease={lease['token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        seen = []
        while "turn_complete" not in seen:
            message = ws.receive()
            if message.get("text"):
                seen.append(__import__("json").loads(message["text"])["type"])
        ws.send_text('{"type": "end"}')
    assert fake.tool_answers and fake.tool_answers[0]["function_responses"][0].response["decided"] == [proposal_id]
    with _db() as db:
        texts = [m.text for m in db.execute(select(NiaMessage).where(NiaMessage.conversation_id == conversation["id"]).order_by(NiaMessage.id)).scalars()]
        assert texts == ["🎙 Genehmige den Brief-Stapel.", "Erledigt, der Stapel ist freigegeben."]
        assert db.get(TeamApproval, proposal_id).status == "approved"  # type: ignore[union-attr]
    # The minutes are costed as the connection closes, just after the socket does.
    for _ in range(30):
        with _db() as db:
            costed = list(db.execute(select(AIUsage.latency_ms).where(AIUsage.operation == "agent.nia.voice")).scalars())
        if costed:
            break
        __import__("time").sleep(0.1)
    assert len(costed) == 1 and costed[0] >= 0
    assert datetime.now(UTC)


class ShowingLive(FakeLive):
    """The owner asks to see the team: Nia opens the page and points at an agent."""

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(server_content=types.LiveServerContent(input_transcription=types.Transcription(text="Show me the team page.")))
        yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
            types.FunctionCall(id="1", name="open_page", args={"path": "/team", "highlight": "agent:outreach"}),
            types.FunctionCall(id="2", name="show_slide", args={"number": 1})]))
        yield types.LiveServerMessage(server_content=types.LiveServerContent(output_transcription=types.Transcription(text="Here is the team."), turn_complete=True))
        await __import__("asyncio").sleep(30)


def test_in_any_voice_conversation_nia_opens_the_page_the_owner_asks_for(admin: Any) -> None:  # noqa: F811 - the fixture
    import json

    _ready()
    fake = ShowingLive(0)
    seen_config: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def connect(**kw: Any) -> AsyncIterator[ShowingLive]:
        seen_config.update(kw)
        yield fake

    voice.CONNECT = connect
    conversation = admin.post("/admin-api/nia/conversations").json()
    lease = admin.post("/admin-api/nia/voice/lease", json={"conversation_id": conversation["id"]}).json()
    controls = []
    with admin.websocket_connect(f"{lease['path']}?lease={lease['token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        while True:
            message = ws.receive()
            if not message.get("text"):
                continue
            data = json.loads(message["text"])
            if data["type"] == "control":
                controls.append(data)
            if data["type"] == "turn_complete":
                break
        ws.send_text('{"type": "end"}')
    assert controls == [{"type": "control", "action": "open_page", "args": {"path": "/team", "highlight": "agent:outreach"}}], "no deck outside a meeting"
    config_text = str(seen_config.get("config"))
    assert "open_page" in config_text and "highlight" in config_text and "show_slide" not in config_text
    assert "/team" in config_text and "/clearance" in config_text, "Nia knows where every page is"


class ReadingLive(FakeLive):
    """Nia reads the owner's screen before she says what is on it."""

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id="s1", name="read_screen", args={})]))
        yield types.LiveServerMessage(server_content=types.LiveServerContent(output_transcription=types.Transcription(text="One call is booked."), turn_complete=True))
        await __import__("asyncio").sleep(30)


@pytest.mark.parametrize("answers", [True, False])
def test_nia_reads_what_the_owner_sees_and_says_so_when_the_page_does_not_answer(admin: Any, answers: bool, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the fixture
    import json

    _ready()
    monkeypatch.setattr(voice, "SCREEN_WAIT_SECONDS", 1)
    fake = ReadingLive(0)

    @contextlib.asynccontextmanager
    async def connect(**_kw: Any) -> AsyncIterator[ReadingLive]:
        yield fake

    voice.CONNECT = connect
    conversation = admin.post("/admin-api/nia/conversations").json()
    lease = admin.post("/admin-api/nia/voice/lease", json={"conversation_id": conversation["id"]}).json()
    with admin.websocket_connect(f"{lease['path']}?lease={lease['token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        while True:
            message = ws.receive()
            if not message.get("text"):
                continue
            data = json.loads(message["text"])
            if data["type"] == "control" and data["action"] == "read_screen" and answers:
                ws.send_text(json.dumps({"type": "screen", "id": data["args"]["id"], "page": "/calls", "text": "Booked calls\nMuster GmbH · 5 Oct 2026, 10:00",
                                         "controls": [{"ref": 1, "kind": "checkbox", "label": "Open for bookings", "checked": True}]}))
            if data["type"] == "turn_complete":
                break
        ws.send_text('{"type": "end"}')
    seen = fake.tool_answers[0]["function_responses"][0].response
    if answers:
        assert seen == {"page": "/calls", "text": "Booked calls\nMuster GmbH · 5 Oct 2026, 10:00",
                        "controls": [{"ref": 1, "kind": "checkbox", "label": "Open for bookings", "checked": True}]}
    else:
        assert "did not answer" in seen["error"]


class ActingLive(FakeLive):
    """The owner says "untick X": Nia unticks it on the page; the save waits for the owner's Allow."""

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
            types.FunctionCall(id="a1", name="page_action", args={"action": "uncheck", "ref": 4})]))
        yield types.LiveServerMessage(server_content=types.LiveServerContent(output_transcription=types.Transcription(text="Press Allow to save."), turn_complete=True))
        await __import__("asyncio").sleep(30)


def test_nia_works_the_page_and_the_owners_page_carries_it_out(admin: Any) -> None:  # noqa: F811 - the fixture
    import json

    _ready()
    fake = ActingLive(0)
    seen_config: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def connect(**kw: Any) -> AsyncIterator[ActingLive]:
        seen_config.update(kw)
        yield fake

    voice.CONNECT = connect
    conversation = admin.post("/admin-api/nia/conversations").json()
    lease = admin.post("/admin-api/nia/voice/lease", json={"conversation_id": conversation["id"]}).json()
    asked: list[dict[str, Any]] = []
    with admin.websocket_connect(f"{lease['path']}?lease={lease['token']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        while True:
            message = ws.receive()
            if not message.get("text"):
                continue
            data = json.loads(message["text"])
            if data["type"] == "control" and data["action"] == "page_action":
                asked.append(data["args"])
                ws.send_text(json.dumps({"type": "screen", "id": data["args"]["id"], "done": "uncheck", "page": "/social",
                                         "waiting_for_owner": "A change waits for the owner's Allow on screen."}))
            if data["type"] == "turn_complete":
                break
        ws.send_text('{"type": "end"}')
    assert asked and (asked[0]["action"], asked[0]["ref"]) == ("uncheck", 4)
    answer = fake.tool_answers[0]["function_responses"][0].response
    assert answer["done"] == "uncheck" and "Allow" in answer["waiting_for_owner"]
    config_text = str(seen_config.get("config"))
    assert "page_action" in config_text and "read_screen" in config_text and "Never guess" in config_text


def test_voice_starts_when_nia_has_a_playbook() -> None:
    """The playbook is read inside its session (a detached row broke every voice start once Nia had one)."""
    from app.models import TeamPlaybook

    _ready()
    with _db() as db:
        db.execute(delete(TeamPlaybook).where(TeamPlaybook.agent == "nia"))
        db.add(TeamPlaybook(agent="nia", version=1, text="Always start with the money.", created_at=datetime.now(UTC)))
    try:
        system, granted, chosen, language = voice.system_prompt(_db)
        assert "Always start with the money." in system and chosen == "Kore" and granted
    finally:
        with _db() as db:
            db.execute(delete(TeamPlaybook).where(TeamPlaybook.agent == "nia"))
