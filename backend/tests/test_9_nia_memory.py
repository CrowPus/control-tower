"""Nia remembers: she knows who she talks with (the owner writes it; she never guesses a name),
picks up where the last talk ended — typed or spoken — writes down what was done together after a conversation goes
quiet, keeps what she did in the browser, and every memory has a topic (the branches of the memory tree)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import (
    AIUsage,
    BrowserSession,
    BrowserStep,
    NiaConversation,
    NiaMemory,
    NiaMessage,
    PlatformSetting,
)
from app.services import platform
from app.services.nia import browser, journal, memory, voice
from app.services.team import config, registry, runner
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    with _db() as db:
        for model in (NiaMemory, NiaMessage, NiaConversation, BrowserStep, BrowserSession):
            db.execute(delete(model))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("agents", memory.OWNER_KEY))))
    yield
    runner.use_model(runner.ProviderModel())


def _ready() -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, "nia", config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0, "tools": list(registry.tools_of(registry.AGENTS["nia"]))}))


class Scripted:
    def __init__(self, answer: str) -> None:
        self.answer, self.system, self.prompt = answer, "", ""

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_more: Any) -> str:
        self.system, self.prompt = system, prompt
        on_usage(runner.Usage("fake-model", 400, 100, 5))
        return self.answer


def _conversation(db: Session, texts: list[tuple[str, str]], *, minutes_ago: int = 30) -> int:
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    conv = NiaConversation(title="t", admin_id=1, created_at=at, updated_at=at)
    db.add(conv)
    db.flush()
    for i, (role, text) in enumerate(texts):
        db.add(NiaMessage(conversation_id=conv.id, role=role, text=text, tool_calls=[], cost_usd=0.0, created_at=at + timedelta(seconds=i)))
    db.flush()
    return conv.id


def test_nia_knows_who_she_talks_with_only_from_the_owner() -> None:
    with _db() as db:
        assert "do not know their name" in memory.owner_block(db), "never a guessed name"
        memory.save_owner(db, {"name": "Alex Owner", "address_as": "Mr Owner", "role": "Founder", "about": "Prefers short answers."})
        block = memory.owner_block(db)
    assert "Alex Owner" in block and "Address them as: Mr Owner." in block and "Prefers short answers." in block


def test_a_new_talk_picks_up_where_the_last_one_ended() -> None:
    _ready()
    with _db() as db:
        memory.save_owner(db, {"name": "Alex Owner", "address_as": "Alex"})
        _conversation(db, [("owner", "Please scan the website for issues."), ("nia", "I found three issues: the header overlaps on scroll …")])
    model = Scripted("Welcome back, Alex.")
    runner.use_model(model)
    from app.services.nia import chat

    with _db() as db:
        conv = NiaConversation(title="", admin_id=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        db.add(conv)
        db.flush()
        new_id = conv.id
    chat.answer(_db, conversation_id=new_id, text="Hello Nia", admin_id=1)
    assert "WHERE YOU LEFT OFF (the end" in model.system and "header overlaps" in model.system and "Address them as: Alex." in model.system
    chat.answer(_db, conversation_id=new_id, text="And now?", admin_id=1)
    assert "WHERE YOU LEFT OFF (the end" not in model.system, "within one conversation its own transcript is the context"
    with _db() as db:
        voice.save(db, voice.VoiceSettings(voice="Kore"))
    spoken, *_rest = voice.system_prompt(_db)
    assert "WHERE YOU LEFT OFF (the end" in spoken and "Address them as: Alex." in spoken, "voice starts where the last talk ended too"


def test_the_journal_writes_down_quiet_conversations_with_topics_once() -> None:
    _ready()
    model = Scripted('Here: [{"kind": "work", "topic": "Website", "text": "Nia scanned the website on 7 Oct: header overlaps on scroll; still open."},'
                     ' {"kind": "person", "topic": "alex", "text": "The owner prefers to be called Alex."}, {"kind": "gossip", "text": "x"}]')
    runner.use_model(model)
    with _db() as db:
        quiet = _conversation(db, [("owner", "Scan the website."), ("nia", "Done: header overlaps.")])
        busy = _conversation(db, [("owner", "Still talking")], minutes_ago=1)
        assert journal.due(db, datetime.now(UTC)) == [quiet], "only conversations that went quiet"
    assert journal.write(_db, quiet) == 2
    with _db() as db:
        rows = {m.kind: m for m in db.execute(select(NiaMemory)).scalars()}
        assert rows["work"].topic == "website" and "header overlaps" in rows["work"].text and rows["person"].topic == "alex"
        assert journal.due(db, datetime.now(UTC)) == [], "read once"
        assert db.get(NiaConversation, busy) is not None
        assert db.execute(select(AIUsage).where(AIUsage.operation == "agent.nia.journal")).first() is not None


def test_a_finished_browser_task_is_remembered_as_work(monkeypatch: pytest.MonkeyPatch) -> None:
    class Quiet(browser.Client):
        def close(self, key: str) -> None:
            return None

    monkeypatch.setattr(browser, "CLIENT", [Quiet()])
    with _db() as db:
        now = datetime.now(UTC)
        row = BrowserSession(key="k1", admin_id=1, goal="Review the website for errors", start_url="https://tower.example", status="running", pending={},
                             turns=3, url="https://www.tower.example/", created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        browser.finish(db, row, "done", "Finished.", result="Three issues: header overlap, a broken link, slow images.")
        kept = db.execute(select(NiaMemory).where(NiaMemory.kind == "work")).scalar_one()
        assert kept.topic == "tower.example" and "header overlap" in kept.text and "Review the website" in kept.text


def test_the_owner_writes_about_themselves_and_sees_the_branches(admin: Any) -> None:  # noqa: F811 - the fixture
    assert admin.get("/admin-api/nia/owner").json() == {"name": "", "address_as": "", "role": "", "about": ""}
    saved = admin.put("/admin-api/nia/owner", json={"name": "Alex Owner", "address_as": "Alex", "role": "Founder", "about": ""})
    assert saved.status_code == 200 and saved.json()["address_as"] == "Alex"
    assert admin.post("/admin-api/nia/memories", json={"kind": "work", "topic": "Letters", "text": "Batch 1 printed and posted."}).status_code == 201
    assert admin.post("/admin-api/nia/memories", json={"kind": "person", "topic": "alex", "text": "Lives in Springfield."}).status_code == 201
    topics = {t["topic"]: t for t in admin.get("/admin-api/nia/memory-topics").json()}
    assert topics["letters"]["kinds"] == {"work": 1} and topics["alex"]["count"] == 1
    only = admin.get("/admin-api/nia/memories", params={"topic": "letters"}).json()
    assert [m["text"] for m in only] == ["Batch 1 printed and posted."]
    with _db() as db:
        assert platform.get(db, memory.OWNER_KEY)["name"] == "Alex Owner"


def test_nia_is_told_what_she_can_do_now_so_an_old_i_cannot_does_not_stick() -> None:
    with _db() as db:
        _conversation(db, [("owner", "Open rule365-2.jpg"), ("nia", "I am unable to open or display image files.")])
        text = memory.context(db, granted=("computer_show", "computer_read", "read_memories"))
    assert "WHAT YOU CAN DO NOW" in text and "OPEN a PDF, picture or text file" in text and "never say you cannot" in text
    assert "may be possible now" in text, "the old refusal is marked as possibly out of date"
    with _db() as db:
        assert "WHAT YOU CAN DO NOW (your tools" not in memory.context(db, granted=("read_memories",)), "only what her tools really allow"


def test_a_long_about_you_saves_and_a_refusal_names_the_field(admin: Any) -> None:  # noqa: F811 - the fixture
    long_about = "I build Acme in Springfield. " * 200
    saved = admin.put("/admin-api/nia/owner", json={"name": "Alex Owner", "address_as": "Alex", "role": "Founder and managing director of Acme", "about": long_about})
    assert saved.status_code == 200, saved.text
    refused = admin.put("/admin-api/nia/owner", json={"name": "x" * 500})
    assert refused.status_code == 422 and refused.json()["error"]["fields"][0]["field"] == "name"
