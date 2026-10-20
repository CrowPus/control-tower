"""Nia's browser: Nia works only when the owner switched it on and watches (no daily budget:
it is the owner's own tool);
safe steps run, steps that send, submit or sign in wait for the owner's yes, password fields go to the owner, blocked
sites are refused; the owner can take over, hand back, answer and stop; and the model always sees every call answered."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.models import AgentTask, BrowserSession, BrowserStep
from app.services import platform
from app.services.nia import browser
from app.services.team import registry, tools
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


def _db() -> AbstractContextManager[Session]:
    return transaction(write=True)


class FakeClient(browser.Client):
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.opened: list[dict[str, Any]] = []
        self.closed: list[str] = []
        self.element: dict[str, Any] = {"tag": "a", "text": "Mehr erfahren", "is_submit": False, "in_form": False, "is_password": False, "is_file": False}

    def open(self, key: str, *, url: str, blocked: tuple[str, ...], keep: tuple[str, ...]) -> dict[str, Any]:
        self.opened.append({"key": key, "url": url, "blocked": blocked, "keep": keep})
        return {"url": url or "about:blank", "screenshot": PNG}

    def act(self, key: str, action: dict[str, Any]) -> dict[str, Any]:
        if action["action"] != "screenshot":
            self.actions.append(action)
        return {"url": action.get("url") or "https://example.com/", "screenshot": PNG, "error": ""}

    def inspect(self, key: str, x: int = -1, y: int = -1) -> dict[str, Any]:
        return {"at": dict(self.element), "focused": None}

    def frame(self, key: str) -> tuple[bytes, str]:
        return b"\xff\xd8jpeg", "https://example.com/"

    def close(self, key: str) -> None:
        self.closed.append(key)


class FakeModel:
    def __init__(self) -> None:
        self.answers: list[list[dict[str, Any]]] = []
        self.seen: list[list[Any]] = []

    def __call__(self, contents: list[Any], system: str) -> tuple[list[dict[str, Any]], int, int]:
        self.seen.append(contents)
        return (self.answers.pop(0) if self.answers else [{"call": "finish", "args": {"report": "Fertig."}}]), 1000, 100


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeClient, FakeModel]]:
    client, model = FakeClient(), FakeModel()
    monkeypatch.setattr(browser, "CLIENT", [client])
    monkeypatch.setattr(browser, "MODEL", [model])
    with _db() as db:
        db.execute(delete(BrowserStep))
        db.execute(delete(BrowserSession))
        platform.put(db, browser.KEY, {"enabled": True, "blocked_sites": ["mybank.de"]})
    yield client, model
    with _db() as db:
        db.execute(delete(BrowserStep))
        db.execute(delete(BrowserSession))
        platform.put(db, browser.KEY, {})


def _start(goal: str = "Finde die Öffnungszeiten des Bürgerbüros Kulmbach", url: str = "") -> int:
    with _db() as db:
        return browser.start(db, admin_id=1, goal=goal, url=url).id


def _turn(session_id: int) -> dict[str, Any]:
    with _db() as db:
        row = db.get(BrowserSession, session_id)
        assert row is not None
        turn = row.turns
    return browser.run_turn(_db, session_id, turn)


def _row(session_id: int) -> BrowserSession:
    with _db() as db:
        row = db.get(BrowserSession, session_id)
        assert row is not None
        db.expunge(row)
        return row


def test_nia_browses_only_when_switched_on_and_one_session_at_a_time(fake: tuple[FakeClient, FakeModel]) -> None:
    with _db() as db:
        platform.put(db, browser.KEY, {})
        with pytest.raises(browser.Refused, match="switched off"):
            browser.start(db, admin_id=1, goal="Suche etwas")
        platform.put(db, browser.KEY, {"enabled": True, "blocked_sites": ["https://www.MyBank.de/login"]})
        assert browser.rules(db).blocked_sites == ("mybank.de",)
        with pytest.raises(browser.Refused, match="blocked"):
            browser.start(db, admin_id=1, goal="Kontostand ansehen", url="https://online.mybank.de")
    first = _start()
    with _db() as db:
        assert db.execute(select(AgentTask).where(AgentTask.task_type == "nia.browse_step", AgentTask.status == "queued")).scalars().first() is not None
        with pytest.raises(browser.Refused, match="already browsing"):
            browser.start(db, admin_id=1, goal="Noch etwas")
    assert _row(first).status == "running"


def test_safe_steps_run_and_the_model_sees_every_call_answered(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    sid = _start(url="kulmbach.de")
    assert _row(sid).start_url == "https://kulmbach.de"
    model.answers = [[{"text": "Ich öffne die Seite."}, {"call": "click_at", "args": {"x": 500, "y": 500}}, {"call": "scroll_document", "args": {"direction": "down"}}]]
    assert _turn(sid) == {"turn": 0}
    assert client.opened[0]["url"] == "https://kulmbach.de" and client.opened[0]["blocked"] == ("mybank.de",)
    assert client.actions == [{"action": "click", "x": 640, "y": 400}, {"action": "scroll", "direction": "down", "amount": 700}]
    assert _turn(sid) == {"done": True}
    row = _row(sid)
    assert row.status == "idle" and row.result == "Fertig." and client.closed == [], "done: the window stays open"
    from google.genai import types

    history = model.seen[1]
    model_turn = [c for c in history if c.role == "model"][0]
    answer = history[history.index(model_turn) + 1]
    assert [p.function_call.name for p in model_turn.parts if p.function_call] == ["click_at", "scroll_document"]
    assert [p.function_response.name for p in answer.parts] == ["click_at", "scroll_document"]
    assert isinstance(answer.parts[-1].function_response.parts[0], types.FunctionResponsePart), "the screen comes with the last answer"
    with _db() as db:
        texts = [s.text for s in db.execute(select(BrowserStep).where(BrowserStep.session_id == sid)).scalars()]
    assert any(t.startswith("Clicks „Mehr erfahren“") for t in texts) and "Finished — the window stays open." in texts


def test_a_submit_click_waits_for_the_owners_yes_and_then_goes_first(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    client.element = {"tag": "button", "text": "Absenden", "is_submit": True, "in_form": True}
    sid = _start()
    model.answers = [[{"call": "click_at", "args": {"x": 100, "y": 100}}, {"call": "wait_5_seconds", "args": {}}]]
    assert _turn(sid) == {"waiting": "ask"}
    row = _row(sid)
    assert row.status == "asking" and client.actions == [] and "Absenden" in row.pending["question"]
    with _db() as db:
        ask = db.execute(select(BrowserStep).where(BrowserStep.session_id == sid, BrowserStep.kind == "ask")).scalar_one()
        assert ask.screenshot == b"\xff\xd8jpeg", "the moment Nia asked is kept with its screen"
        assert browser.run_turn(_db, sid, row.turns) == {"skipped": True}, "nothing moves while Nia waits"
        browser.answer(db, db.get(BrowserSession, sid), "allow")  # type: ignore[arg-type]
    model.answers = [[{"call": "finish", "args": {"report": "Formular gesendet."}}]]
    assert _turn(sid) == {"done": True}
    assert client.actions == [{"action": "click", "x": 128, "y": 80}], "the allowed click, once"
    from google.genai import types  # noqa: F401 - the history below is built from these

    answer = [c for c in model.seen[-1] if c.role == "user"][1]
    assert [p.function_response.name for p in answer.parts] == ["click_at", "wait_5_seconds"]
    assert answer.parts[1].function_response.response.get("skipped"), "the call after the asked one was not carried out, and the model is told"


def test_a_password_field_goes_to_the_owner_and_nia_never_types_it(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    client.element = {"tag": "input", "type": "password", "is_password": True, "in_form": True, "text": ""}
    sid = _start()
    model.answers = [[{"call": "type_text_at", "args": {"x": 500, "y": 300, "text": "geheim123"}}]]
    assert _turn(sid) == {"waiting": "owner"}
    row = _row(sid)
    assert row.status == "owner" and "password" in row.reason and client.actions == []
    with _db() as db:
        row_db = db.get(BrowserSession, sid)
        assert row_db is not None
        browser.owner_action(db, row_db, {"action": "type", "x": 640, "y": 240, "text": "my-real-password"})
        steps = " ".join(s.text for s in db.execute(select(BrowserStep).where(BrowserStep.session_id == sid)).scalars())
        assert "my-real-password" not in steps and "typed something" in steps, "what the owner types is never written down"
        browser.answer(db, row_db, "hand_back", note="Ich habe mich angemeldet.")
    model.answers = [[{"call": "finish", "args": {"report": "Angemeldet und gefunden."}}]]
    assert _turn(sid) == {"done": True}
    told = " ".join(p.text for c in model.seen[-1] for p in c.parts if getattr(p, "text", None))
    assert "Ich habe mich angemeldet" in told, "Nia hears what the owner did"


def test_blocked_sites_and_odd_addresses_are_refused_and_nia_goes_on(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    sid = _start()
    model.answers = [[{"call": "navigate", "args": {"url": "https://www.mybank.de/konto"}}, {"call": "navigate", "args": {"url": "file:///etc/passwd"}}]]
    assert _turn(sid) == {"turn": 0}
    assert client.actions == []
    with _db() as db:
        step = db.execute(select(BrowserStep).where(BrowserStep.session_id == sid, BrowserStep.kind == "turn")).scalar_one()
        assert all("Refused" in r["response"]["error"] for r in step.responses)


def test_a_risky_word_or_the_models_own_warning_asks_first(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    client.element = {"tag": "a", "text": "Jetzt kaufen", "in_form": False, "is_submit": False}
    sid = _start()
    model.answers = [[{"call": "click_at", "args": {"x": 10, "y": 10}}]]
    assert _turn(sid) == {"waiting": "ask"}
    with _db() as db:
        browser.answer(db, db.get(BrowserSession, sid), "stop")  # type: ignore[arg-type]
    assert _row(sid).status == "stopped" and client.actions == []
    client.element = {"tag": "a", "text": "Weiter", "in_form": False}
    sid2 = _start()
    model.answers = [[{"call": "click_at", "args": {"x": 10, "y": 10, "safety_decision": {"decision": "require_confirmation", "explanation": "x"}}}]]
    assert _turn(sid2) == {"waiting": "ask"}
    assert _row(sid2).pending["safety"] is True


def test_nobody_watching_pauses_and_watching_again_goes_on(fake: tuple[FakeClient, FakeModel]) -> None:
    sid = _start()
    with _db() as db:
        row = db.get(BrowserSession, sid)
        assert row is not None
        row.watched_at = datetime.now(UTC) - timedelta(minutes=5)
    assert _turn(sid) == {"paused": "unwatched"}
    with _db() as db:
        row = db.get(BrowserSession, sid)
        assert row is not None
        browser.watched(db, row)
        assert row.status == "running"


def test_the_owners_limits_stop_a_session(fake: tuple[FakeClient, FakeModel]) -> None:
    sid = _start()
    with _db() as db:
        platform.put(db, browser.KEY, {"enabled": True, "max_turns": 1})
        row = db.get(BrowserSession, sid)
        assert row is not None
        row.turns = 1
    with _db() as db:
        assert browser.run_turn(_db, sid, 1) == {"stopped": "turns"}
    assert "1 steps" in _row(sid).reason


def test_nia_starts_the_browser_only_for_the_owner_talking_to_her(fake: tuple[FakeClient, FakeModel]) -> None:
    nia = registry.AGENTS["nia"]
    assert {"browse", "browser_status", "browser_answer"} <= set(nia.tools) and all(t in tools.CATALOGUE for t in ("browse", "browser_status", "browser_answer"))
    away = tools.Context(agent=nia, run_id=1, db=_db, granted=("browse",))
    assert "error" in tools.run(away, "browse", {"goal": "Suche die Öffnungszeiten"})
    here = tools.Context(agent=nia, run_id=1, db=_db, granted=("browse", "browser_status", "browser_answer"), admin_id=1)
    started = tools.run(here, "browse", {"goal": "Suche die Öffnungszeiten", "url": "kulmbach.de"})
    assert started.get("session_id") and "watch" in started["tell_the_owner"]
    assert tools.run(here, "browser_status", {})["session"]["status"] == "running"
    assert tools.run(here, "browser_answer", {"decision": "stop"})["status"] == "stopped"


def test_the_owner_watches_answers_takes_over_and_sets_the_rules(admin: Any, fake: tuple[FakeClient, FakeModel]) -> None:  # noqa: F811 - the fixture
    client, model = fake
    rules = admin.put("/admin-api/nia/browser/rules", json={"enabled": True, "max_minutes": 20, "max_turns": 80,
                                                            "blocked_sites": ["mybank.de", "https://paypal.com"], "keep_signed_in": ["elster.de"]})
    assert rules.status_code == 200, rules.text
    assert rules.json()["blocked_sites"] == ["mybank.de", "paypal.com"]
    started = admin.post("/admin-api/nia/browser/sessions", json={"goal": "Prüfe die Steuerfristen 2026", "url": "https://www.elster.de"})
    assert started.status_code == 201, started.text
    sid = started.json()["id"]
    assert admin.post("/admin-api/nia/browser/sessions", json={"goal": "Noch etwas anderes"}).status_code == 409
    frame = admin.get(f"/admin-api/nia/browser/sessions/{sid}/frame")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    model.answers = [[{"call": "click_at", "args": {"x": 500, "y": 500}}]]
    _turn(sid)
    acted = admin.post(f"/admin-api/nia/browser/sessions/{sid}/act", json={"action": "type", "x": 10, "y": 10, "text": "secret"})
    assert acted.status_code == 200, acted.text
    view = admin.get(f"/admin-api/nia/browser/sessions/{sid}").json()
    assert view["status"] == "owner" and "secret" not in str(view)
    assert admin.post(f"/admin-api/nia/browser/sessions/{sid}/act", json={"action": "navigate", "url": "https://paypal.com/x"}).status_code == 409
    assert admin.post(f"/admin-api/nia/browser/sessions/{sid}/answer", json={"decision": "stop"}).json()["status"] == "stopped"
    overview = admin.get("/admin-api/nia/browser").json()
    assert overview["active"] is None and overview["recent"][0]["id"] == sid and overview["rules"]["keep_signed_in"] == ["elster.de"]


def test_both_gemini_action_sets_are_understood() -> None:
    assert browser.translate("click", {"x": 500, "y": 963, "intent": "Learn more"}) == {"action": "click", "x": 640, "y": 770}
    assert browser.translate("click_at", {"x": 500, "y": 500}) == {"action": "click", "x": 640, "y": 400}
    assert browser.translate("type", {"text": "Kulmbach", "press_enter": True}) == {"action": "type", "text": "Kulmbach", "press_enter": True}
    assert browser.translate("hotkey", {"keys": ["ctrl", "a"]}) == {"action": "key", "keys": "Control+a"}
    assert browser.translate("key_combination", {"keys": "control+c"}) == {"action": "key", "keys": "Control+c"}
    assert browser.translate("press_key", {"key": "enter"}) == {"action": "key", "keys": "Enter"}
    assert browser.translate("right_click", {"x": 1, "y": 1}) is None and browser.translate("drag_and_drop", {}) is None


def test_enter_in_a_search_box_needs_no_yes_but_in_a_form_it_does() -> None:
    rules = browser.Rules(enabled=True)
    typing = {"action": "type", "text": "Acme", "press_enter": True}
    assert browser.check(typing, {"at": None, "focused": {"in_form": True, "is_search": True}}, rules, "")[0] == "go"
    assert browser.check(typing, {"at": None, "focused": {"in_form": True, "is_search": False}}, rules, "")[0] == "ask"
    assert browser.check({"action": "type", "text": "x"}, {"at": None, "focused": {"is_password": True}}, rules, "")[0] == "owner"


def test_nia_searches_the_web_without_a_search_engines_page(fake: tuple[FakeClient, FakeModel], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import websearch as search

    client, model = fake
    found = search.Found(answer="Die Stadt Kulmbach …", sources=[{"title": "Kulmbach", "url": "https://www.kulmbach.de"}], queries=["Bürgerbüro Kulmbach"],
                         model="gemini", input_tokens=10, output_tokens=5, latency_ms=1)
    monkeypatch.setattr(search, "SEARCH", lambda query, tier: found)
    sid = _start()
    model.answers = [[{"call": "web_search", "args": {"query": "Bürgerbüro Kulmbach Öffnungszeiten"}}]]
    assert _turn(sid) == {"turn": 0}
    with _db() as db:
        step = db.execute(select(BrowserStep).where(BrowserStep.session_id == sid, BrowserStep.kind == "turn")).scalar_one()
        assert step.responses[0]["response"]["sources"][0]["url"] == "https://www.kulmbach.de" and client.actions == []


def test_the_owner_types_straight_into_the_browser_without_a_line_per_key(fake: tuple[FakeClient, FakeModel]) -> None:
    client, _model = fake
    sid = _start()
    with _db() as db:
        row = db.get(BrowserSession, sid)
        assert row is not None
        browser.owner_action(db, row, {"action": "click", "x": 300, "y": 200})
        for chunk in ("alex", "@acme", ".example"):
            browser.owner_action(db, row, {"action": "type", "text": chunk})
        browser.owner_action(db, row, {"action": "key", "keys": "Tab"})
        browser.owner_action(db, row, {"action": "type", "text": "s3cret!"})
        texts = [s.text for s in db.execute(select(BrowserStep).where(BrowserStep.session_id == sid, BrowserStep.kind == "owner")).scalars()]
    assert texts == ["The owner clicked.", "The owner typed something.", "The owner pressed Tab.", "The owner typed something."]
    assert [a["text"] for a in client.actions if a["action"] == "type"] == ["alex", "@acme", ".example", "s3cret!"], "every key arrives, in order"
    assert "s3cret" not in " ".join(texts)



def test_the_window_stays_open_and_the_next_task_goes_on_in_it(fake: tuple[FakeClient, FakeModel]) -> None:
    client, model = fake
    first = _start(url="kulmbach.de")
    model.answers = [[{"call": "finish", "args": {"report": "Gefunden."}}]]
    _turn(first)
    old = _row(first)
    assert old.status == "idle" and client.closed == []
    with _db() as db:
        assert browser.active(db).id == first  # type: ignore[union-attr]
        browser.owner_action(db, db.get(BrowserSession, first), {"action": "click", "x": 5, "y": 5})  # type: ignore[arg-type]
        assert db.get(BrowserSession, first).status == "idle", "the owner clicks around without taking a task over"  # type: ignore[union-attr]
        with pytest.raises(browser.Refused, match="finished"):
            browser.answer(db, db.get(BrowserSession, first), "allow")  # type: ignore[arg-type]
    second = _start(goal="Jetzt die Öffnungszeiten der Stadtbücherei", url="https://www.kulmbach.de/buecherei")
    new, before = _row(second), _row(first)
    assert new.key == old.key and before.status == "done" and before.key != old.key, "the same window carries the next task"
    model.answers = [[{"call": "finish", "args": {"report": "Mo-Fr 9-18 Uhr."}}]]
    _turn(second)
    assert client.opened[-1]["key"] == old.key and client.opened[-1]["url"] == "https://www.kulmbach.de/buecherei"
    with _db() as db:
        browser.answer(db, db.get(BrowserSession, second), "stop")  # type: ignore[arg-type]
    assert _row(second).status == "stopped" and client.closed == [old.key], "closing the window is the owner's"
