"""Agents that think for a while (seen live 2026-10-09): an empty answer from Gemini does not end an agent's run, and
no admin transaction sits open while an agent answers in its chat (the database ends a transaction idle for a minute,
and the owner got "Database operation failed" although the agent had answered)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, text

from app.config import settings
from app.db import admin_transaction, transaction
from app.models import AIUsage, PlatformSetting, TeamConversation
from app.services.marketing import video_studio
from app.services.marketing.character import Character
from app.services.team import config, registry, runner
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    runner.use_model(runner.ProviderModel())
    with _db() as db:
        db.execute(delete(TeamConversation))
        db.execute(delete(AIUsage).where(AIUsage.operation.like("agent.%")))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))


# --- an empty answer is asked again --------------------------------------------------------------------------
def _answer(*, call: str = "", text_: str = "") -> Any:
    from google.genai import types

    parts = []
    if call:
        parts.append(types.Part(function_call=types.FunctionCall(id="c1", name=call, args={})))
    if text_:
        parts.append(types.Part(text=text_))
    candidate = SimpleNamespace(content=types.Content(role="model", parts=parts) if parts else None, finish_reason="STOP")
    return SimpleNamespace(candidates=[candidate], usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=0 if not parts else 5),
                           function_calls=[p.function_call for p in parts if p.function_call] or None)


def test_an_empty_answer_from_the_model_is_asked_again_instead_of_ending_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [_answer(call="video_studio"), _answer(), _answer(call="propose"), _answer(text_="Proposed one concept.")]
    seen: list[list[Any]] = []

    class Models:
        def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
            seen.append(list(contents))
            return answers.pop(0)

    class Client:
        def __init__(self, **_kw: Any) -> None:
            self.models = Models()

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr("google.genai.Client", Client)
    monkeypatch.setattr(settings(), "gemini_api_key", "test-key")
    called: list[str] = []
    rounds: list[int] = []
    out = runner.ProviderModel().converse(tier="fast", system="s", prompt="p", declarations=[{"name": "video_studio", "description": "d", "parameters": {"type": "OBJECT", "properties": {}}},
                                                                                         {"name": "propose", "description": "d", "parameters": {"type": "OBJECT", "properties": {}}}],
                                        call_tool=lambda name, args: called.append(name) or {"ok": True}, before_round=lambda: rounds.append(1),
                                        on_usage=lambda u: None, max_rounds=8)
    assert called == ["video_studio", "propose"], "the run went on after the empty answer and proposed"
    assert out == "Proposed one concept." and not answers
    nudged = seen[2][-1]
    assert nudged.role == "user" and runner.EMPTY_NUDGE in [p.text for p in nudged.parts if p.text], "asked again in the same user turn"


# --- the chat keeps no transaction open while the agent thinks -----------------------------------------------
def test_no_admin_transaction_is_open_while_an_agent_answers_in_its_chat(admin: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    with _db() as db:
        config.save_team(db, config.TeamSettings(enabled=True, language="en", ai_daily_usd=5.0))
        config.save_agent(db, "graphic_designer", config.AgentSettings.model_validate({"enabled": True, "tier": "fast", "ai_daily_usd": 1.0,
                                                                                     "tools": list(registry.tools_of(registry.AGENTS["graphic_designer"]))}))
        now = datetime.now(UTC)
        row = TeamConversation(agent="graphic_designer", title="", created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        conversation = row.id
    idle: list[int] = []

    class Thinker:
        def converse(self, *, before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_kw: Any) -> str:
            before_round()
            on_usage(runner.Usage("fake", 10, 10, 5))
            with admin_transaction() as session:
                if session.get_bind().dialect.name == "postgresql":
                    idle.append(int(session.execute(text("select count(*) from pg_stat_activity where state = 'idle in transaction' "
                                                         "and datname = current_database() and pid <> pg_backend_pid()")).scalar() or 0))
            return "Here is a concept."

    runner.use_model(Thinker())
    said = admin.post(f"/admin-api/team/agents/graphic_designer/conversations/{conversation}/messages", json={"text": "why is no video ready"})
    assert said.status_code == 200, said.text
    assert said.json()["messages"][-1]["text"] == "Here is a concept."
    assert idle in ([], [0]), "no transaction waits while the agent thinks (PostgreSQL ends one idle for a minute)"


# --- the video model gets no separate negative prompt -----------------------------------------------------------
def test_the_scene_prompt_carries_what_to_avoid_and_no_negative_prompt_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class Models:
        def generate_videos(self, *, model: str, prompt: str, config: Any, image: Any = None) -> Any:
            sent.update(prompt=prompt, config=config, image=image)
            return SimpleNamespace(name="operations/x")

    monkeypatch.setattr(video_studio, "_client", lambda: SimpleNamespace(models=Models()))
    figure = Character(name="Mo", look="curly hair", picture_ids=[1])
    concept = {"idea": "Mo and the inbox", "scenes": [{"seconds": 8, "shot": "Mo at a desk, close-up, morning light", "dialogue": "Wir melden uns?"}]}
    prompt = video_studio.scene_prompt(concept, 0, figure, "warm")
    assert video_studio.AVOID in prompt and "Mo" in prompt
    assert video_studio._start(prompt, "veo-3.1-fast-generate-preview", [(b"png", "image/png")], 8) == "operations/x"
    assert sent["config"].negative_prompt is None and len(sent["config"].reference_images) == 1 and sent["image"] is None
    video_studio._start(prompt, "veo-3.1-fast-generate-preview", [(b"png", "image/png")], 8, (b"jpg", "image/jpeg"))
    assert sent["image"].image_bytes == b"jpg" and sent["config"].reference_images is None, "from its first frame, without reference pictures"
