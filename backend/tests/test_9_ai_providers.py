"""The owner's AI provider (Admin → Platform → AI): Gemini, Claude or OpenAI with the owner's key (stored encrypted,
never shown), models and prices; the agents' tool loop and structured answers over Claude and OpenAI; the test button;
and the first-run setup."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import delete

from app.ai import providers, vendors
from app.ai.client import gemini, reset_for_tests
from app.db import transaction
from app.models import PlatformSetting
from app.services.team import runner
from tests.test_9_team import admin  # noqa: F401 - the signed-in owner (module fixture)

CLAUDE_KEY = "sk-ant-test-0123456789abcdefghijklmnop"
OPENAI_KEY = "sk-test-0123456789abcdefghijklmnopqrstu"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with transaction(write=True) as db:
            db.execute(delete(PlatformSetting).where(PlatformSetting.key.in_(("ai", "setup", "brand", "agents"))))
        providers.remember({})

    wipe()
    reset_for_tests()
    yield
    wipe()
    vendors.TRANSPORT = None
    import app.ai.client as client

    client._service = None
    reset_for_tests()


def _use(provider: str) -> None:
    keys = {"anthropic": CLAUDE_KEY, "openai": OPENAI_KEY}
    from app.core import crypto

    providers.remember({"provider": provider, "keys": {provider: crypto.encrypt(keys[provider], associated_data=f"ai-key:{provider}")},
                        "models": {"openai": {"reasoning": "gpt-test", "fast": "gpt-test-mini"}}})


class FakeVendor:
    """Claude's and OpenAI's APIs, enough for a tool loop: a tool call first, then an answer."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        if request.url.host == "api.anthropic.com":
            answered = any(isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"]) for m in body["messages"])
            if body.get("tools") and not answered:
                content = [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "tu1", "name": "read_insights", "input": {"days": 7}}]
            else:
                content = [{"type": "text", "text": '{"ok": true, "note": "Done."}' if "JSON schema" in body["system"] else "Done: 3 cleared."}]
            return httpx.Response(200, json={"content": content, "usage": {"input_tokens": 100, "output_tokens": 20}})
        answered = any(m["role"] == "tool" for m in body["messages"])
        if body.get("tools") and not answered:
            message = {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_insights", "arguments": '{"days": 7}'}}]}
        else:
            message = {"content": '{"ok": true, "note": "Done."}' if body.get("response_format") else "Done: 3 cleared."}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": {"prompt_tokens": 90, "completion_tokens": 10}})


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_the_agents_tool_loop_runs_on_claude_and_openai(provider: str) -> None:
    _use(provider)
    fake = FakeVendor()
    vendors.TRANSPORT = httpx.MockTransport(fake)
    calls: list[tuple[str, dict[str, Any]]] = []
    usage: list[runner.Usage] = []
    decls = [{"name": "read_insights", "description": "Numbers.", "parameters": {"type": "OBJECT", "properties": {"days": {"type": "INTEGER"}}}}]
    text = runner.MODEL.converse(tier="fast", system="You are an agent.", prompt="Work.", declarations=decls,
                                 call_tool=lambda n, a: calls.append((n, a)) or {"cleared": 3}, before_round=lambda: None, on_usage=usage.append)
    assert text == "Done: 3 cleared." and calls == [("read_insights", {"days": 7})]
    assert len(usage) == 2 and usage[0].model == ("claude-haiku-5-5" if provider == "anthropic" else "gpt-test-mini")
    first = fake.bodies[0]
    tools = first["tools"]
    if provider == "anthropic":
        assert tools[0]["input_schema"] == {"type": "object", "properties": {"days": {"type": "integer"}}}
        assert fake.headers[0]["x-api-key"] == CLAUDE_KEY
        assert fake.bodies[1]["messages"][-1]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": '{"cleared": 3}'}
    else:
        assert tools[0]["function"]["parameters"]["properties"]["days"]["type"] == "integer"
        assert fake.headers[0]["authorization"] == f"Bearer {OPENAI_KEY}"
        assert fake.bodies[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"cleared": 3}'}


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_structured_answers_come_from_the_chosen_provider(provider: str) -> None:
    class Answer(BaseModel):
        ok: bool = False
        note: str = ""

    _use(provider)
    vendors.TRANSPORT = httpx.MockTransport(FakeVendor())
    import app.ai.client as client

    client._service = None
    parsed, result = gemini().structured(Answer, operation="test.structured", system="Check.", prompt="Is it fine?", tier="reasoning")
    assert parsed == Answer(ok=True, note="Done.") and result.model == ("claude-sonnet-5-5" if provider == "anthropic" else "gpt-test")


def test_a_provider_error_is_classified_and_the_key_never_shown() -> None:
    _use("anthropic")
    vendors.TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": f"invalid x-api-key {CLAUDE_KEY}"}}))
    with pytest.raises(providers.ProviderError) as caught:
        runner.MODEL.converse(tier="fast", system="s", prompt="p", declarations=[], call_tool=lambda n, a: {}, before_round=lambda: None,
                              on_usage=lambda u: None)
    from app.ai.client import _classify, error_detail

    assert caught.value.code == 401 and _classify(caught.value) == (False, "client_error_401")
    assert CLAUDE_KEY not in error_detail(caught.value)


def test_the_owner_chooses_the_provider_with_its_key_models_and_prices(admin: Any) -> None:  # noqa: F811
    out = admin.get("/admin-api/ai").json()
    assert out["provider"] == "gemini" and out["configured"] is False and "Nia's voice" in out["gemini_only"]
    refused = admin.put("/admin-api/ai", json={"provider": "anthropic"})
    assert refused.status_code == 422 and "key" in refused.text
    assert providers.provider() == "gemini", "a refused change is not kept"
    no_price = admin.put("/admin-api/ai", json={"provider": "anthropic", "keys": {"anthropic": CLAUDE_KEY}})
    assert no_price.status_code == 422 and "price of claude-sonnet-5-5" in no_price.text
    saved = admin.put("/admin-api/ai", json={"provider": "anthropic", "keys": {"anthropic": CLAUDE_KEY},
                                             "prices": {"claude-sonnet-5-5": [3, 15], "claude-haiku-5-5": [1, 5]}})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    claude = next(p for p in body["providers"] if p["key"] == "anthropic")
    assert body["provider"] == "anthropic" and body["configured"] and claude["key_source"] == "admin" and CLAUDE_KEY not in saved.text
    assert body["prices"]["claude-haiku-5-5"] == [1, 5]
    from app.ai.client import estimate_cost

    assert estimate_cost(1_000_000, 1_000_000, "claude-haiku-5-5") == 6.0
    vendors.TRANSPORT = httpx.MockTransport(FakeVendor())
    import app.ai.client as client

    client._service = None
    tested = admin.post("/admin-api/ai/test").json()
    assert tested["ok"] and tested["model"] == "claude-haiku-5-5" and tested["message"] == "Done: 3 cleared."
    kept = admin.put("/admin-api/ai", json={"provider": "anthropic", "keys": {"anthropic": ""}, "prices": body["prices"]})
    assert kept.status_code == 200 and providers.key("anthropic") == CLAUDE_KEY, "an empty key keeps the one set"
    assert admin.put("/admin-api/ai", json={"provider": "anthropic", "openai_base_url": "http://evil.example"}).status_code == 422
    log = admin.get("/admin-api/audit").text
    assert "ai.changed" in log and "ai.tested" in log and CLAUDE_KEY not in log


def test_the_first_run_setup_lists_what_is_missing_and_finishes_only_when_ready(admin: Any) -> None:  # noqa: F811
    steps = {s["key"]: s for s in admin.get("/admin-api/setup").json()["steps"]}
    assert not steps["company"]["done"] and not steps["ai"]["done"] and steps["company"]["required"] and not steps["agent"]["required"]
    refused = admin.post("/admin-api/setup/finish")
    assert refused.status_code == 422 and "Your company" in refused.text
    assert admin.put("/admin-api/setup/company", json={"name": "Acme", "about": "Too short"}).status_code == 422
    done = admin.put("/admin-api/setup/company", json={"name": "Acme", "about": "Acme sells handmade lamps to small hotels in Europe."}).json()
    assert {s["key"]: s["done"] for s in done["steps"]}["company"]
    _use("openai")
    with transaction(write=True) as db:
        from app.services.platform import put

        put(db, "ai", providers.stored() | {"prices": {"gpt-test": [1, 2]}})
    admin.put("/admin-api/team/settings", json={"enabled": True, "language": "en", "ai_daily_usd": 2})
    finished = admin.post("/admin-api/setup/finish")
    assert finished.status_code == 200 and finished.json()["finished"] is True
