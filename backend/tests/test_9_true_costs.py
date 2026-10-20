"""What the AI really costs (2026-10-10): each model at its own price, voice by the minutes heard and spoken, and the
team's cost with its pictures and videos — on the Team page, per agent, in the Office and for the agents."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete

from app.ai.client import estimate_cost, voice_cost
from app.config import settings
from app.models import AIUsage, MarketingMedia
from app.services.office import state as office
from app.services.team import costs
from tests.test_9_team import (  # noqa: F401 - the owner signed in to the admin app (fixture)
    _db,
    admin,
)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        db.execute(delete(AIUsage))
        db.execute(delete(MarketingMedia))


def test_each_model_is_priced_at_its_own_list_price() -> None:
    assert estimate_cost(1_000_000, 1_000_000, "gemini-3.8-flash") == pytest.approx(0.75 + 3.75)
    assert estimate_cost(1_000_000, 1_000_000, "gemini-3.5-flash-lite") == pytest.approx(0.30 + 2.50)
    assert estimate_cost(1_000_000, 0, "some-other-model") == pytest.approx(settings().ai_input_price_per_million_usd)


def test_voice_is_priced_by_the_minutes_heard_and_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    assert voice_cost(60, 60) == pytest.approx(0.005 + 0.018), "it was $0: the price per minute was never set"
    monkeypatch.setattr(settings(), "ai_voice_price_per_minute_usd", 0.02)
    assert voice_cost(60, 60) == pytest.approx(0.04), "the older setting prices every minute alike"


def _usage(operation: str, usd: float) -> None:
    with _db() as db:
        db.add(AIUsage(agent_type="team", model="m", operation=operation, estimated_cost_usd=usd, created_at=datetime.now(UTC)))


def _seed() -> None:
    _usage("agent.qa_runner.run", 0.30)
    _usage("agent.graphic_designer.run", 0.05)
    _usage("marketing.video", 2.40)  # what the Team page left out
    _usage("marketing.video_review", 0.01)
    _usage("marketing.image", 0.07)
    _usage("marketing.image", 0.07)
    with _db() as db:
        now = datetime.now(UTC)
        db.add(MarketingMedia(kind="image", source="generated", title="Lager", mime_type="image/png", data=b"x", public_token=secrets.token_urlsafe(24),
                              cost_usd=0.07, created_by="content_community", created_at=now))
        db.add(MarketingMedia(kind="image", source="upload", title="Mine", mime_type="image/png", data=b"x", public_token=secrets.token_urlsafe(24),
                              cost_usd=0.07, created_by="admin:1", created_at=now))


def test_the_team_cost_has_its_videos_and_pictures_and_each_agent_its_own() -> None:
    _seed()
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with _db() as db:
        assert costs.team(db, day) == {"total": pytest.approx(2.90), "agents": pytest.approx(0.35), "pictures": pytest.approx(0.14), "videos": pytest.approx(2.41)}
        assert costs.agent(db, "graphic_designer", day) == pytest.approx(2.46), "its own work and its videos"
        assert costs.agent(db, "content_community", day) == pytest.approx(0.07), "the picture it made"
        assert costs.agent(db, "qa_runner", day) == pytest.approx(0.30)
        seen = {a["key"]: a["cost_today_usd"] for a in office.state(db)["agents"]}
        assert seen["graphic_designer"] == pytest.approx(2.46) and seen["content_community"] == pytest.approx(0.07), "the Office shows the same"


def test_the_team_page_shows_what_the_team_really_cost(admin: Any) -> None:  # noqa: F811 - the fixture
    _seed()
    page = admin.get("/admin-api/team").json()
    assert page["cost_today_usd"] == pytest.approx(2.90) and page["cost_month_usd"] == pytest.approx(2.90)
    assert page["cost_today"] == {"total": pytest.approx(2.90), "agents": pytest.approx(0.35), "pictures": pytest.approx(0.14), "videos": pytest.approx(2.41)}
    designer = next(a for a in page["agents"] if a["key"] == "graphic_designer")
    assert designer["cost_today_usd"] == pytest.approx(2.46) and designer["cost_7d_usd"] == pytest.approx(2.46)
