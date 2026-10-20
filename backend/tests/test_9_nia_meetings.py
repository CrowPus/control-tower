"""Meetings with Nia (agent team, stage A2c): no meeting unless the owner schedules one; the deck
is prepared at the owner's time with numbers frozen from live data; Nia opens only admin pages it
may; a weekly meeting schedules the next; the deck downloads as a PDF."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.db import transaction
from app.models import NiaMeeting, PlatformSetting
from app.services.nia import meetings, voice
from app.services.team import config, registry, runner
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


class Builder:
    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
                 before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_more: Any) -> str:
        self.prompt = prompt
        self.results = [call_tool("add_slide", {"kind": "title", "title": "Wochenrunde", "talk": "Willkommen."}),
                        call_tool("add_slide", {"kind": "metric", "title": "Freigegeben", "talk": "So viele.", "metric": "cleared", "days": 7}),
                        call_tool("add_slide", {"kind": "page", "title": "Die Liste", "talk": "Schauen wir rein.", "path": "/clearance", "query": "status=pending"}),
                        call_tool("add_slide", {"kind": "page", "title": "Nope", "talk": "", "path": "/admins"}),
                        call_tool("add_slide", {"kind": "metric", "title": "Nope", "talk": "", "metric": "made_up"})]
        return "Deck ready."


def _ready() -> None:
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="de", ai_daily_usd=5.0))
        config.save_agent(db, "nia", config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0, "tools": list(registry.tools_of(registry.AGENTS["nia"]))}))
        voice.save(db, voice.VoiceSettings(voice="Kore"))


def test_the_deck_is_prepared_at_the_owners_time_and_a_weekly_meeting_repeats() -> None:
    with _db() as db:
        db.execute(delete(NiaMeeting))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("agents", "nia"))))
    _ready()
    model = Builder()
    runner.use_model(model)
    now = datetime.now(UTC)
    with _db() as db:
        meeting = NiaMeeting(title="Wochenrunde", starts_at=now + timedelta(hours=3), minutes=30, agenda="Briefe", prepare_hours=2, repeat_weekly=True, status="scheduled",
                             deck=[], created_at=now, updated_at=now)
        db.add(meeting)
        db.flush()
        meeting_id = meeting.id
    try:
        assert meetings.tick(_db, now)["prepared"] == [], "not yet: the owner said 2 hours before"
        done = meetings.tick(_db, now + timedelta(hours=1, minutes=5))
        assert done["prepared"][0]["slides"] == 3 and "Briefe" in model.prompt
        assert "error" in model.results[3] and "error" in model.results[4], "only allowed pages and real KPIs"
        with _db() as db:
            row = db.get(NiaMeeting, meeting_id)
            assert row is not None and row.status == "prepared" and row.deck[1]["data"]["metric"] == "cleared" and len(row.deck[1]["data"]["series"]) == 7
            assert row.deck[2]["data"] == {"path": "/clearance", "query": "status=pending", "highlight": ""}
            assert meetings.deck_pdf(row).startswith(b"%PDF")
            text = voice.deck_text(db, meeting_id)
            assert "1. [title] Wochenrunde" in text and "show_slide" in text
        over = meetings.tick(_db, now + timedelta(hours=4))
        assert over["held"] == [meeting_id]
        with _db() as db:
            nxt = db.execute(select(NiaMeeting).where(NiaMeeting.status == "scheduled")).scalar_one()
            assert nxt.starts_at == now + timedelta(hours=3, days=7) and nxt.deck == []
    finally:
        runner.use_model(runner.GeminiModel())


def test_the_owner_schedules_and_presents(admin: Any) -> None:  # noqa: F811 - the fixture
    with _db() as db:
        db.execute(delete(NiaMeeting))
    assert admin.post("/admin-api/nia/meetings", json={"title": "x", "starts_at": "2020-01-01T10:00:00+00:00", "minutes": 30, "prepare_hours": 2, "repeat_weekly": False}).status_code in (400, 422)
    assert admin.post("/admin-api/nia/meetings", json={"title": "x", "starts_at": "2099-01-01T10:00:00+00:00"}).status_code == 422, "no defaults: length and preparation time are the owner's"
    made = admin.post("/admin-api/nia/meetings", json={"title": "Montagsrunde", "starts_at": "2099-01-05T08:00:00+00:00", "minutes": 30, "agenda": "", "prepare_hours": 12, "repeat_weekly": True})
    assert made.status_code == 201
    meeting = made.json()
    refused = admin.post(f"/admin-api/nia/meetings/{meeting['id']}/present")
    assert refused.status_code in (400, 422) and "not ready" in refused.text
    with _db() as db:
        row = db.get(NiaMeeting, meeting["id"])
        assert row is not None
        row.deck = [{"kind": "title", "title": "Hallo", "talk": "", "bullets": [], "data": {}}]
    _ready()
    lease = admin.post(f"/admin-api/nia/meetings/{meeting['id']}/present").json()
    assert lease["path"] == "/admin-api/nia/voice/live" and voice.take(lease["token"]).meeting_id == meeting["id"]  # type: ignore[union-attr]
    assert admin.get(f"/admin-api/nia/meetings/{meeting['id']}").json()["conversation_id"] is not None
    assert admin.get(f"/admin-api/nia/meetings/{meeting['id']}/pdf").content.startswith(b"%PDF")
    assert admin.delete(f"/admin-api/nia/meetings/{meeting['id']}").json()["status"] == "cancelled"
