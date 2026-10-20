"""Hosted outside agents: the install runs them on its own server with the owner's own key for
their provider — Anthropic (Claude), OpenAI (GPT) or Google (Gemini) — on the owner's schedule, with the same tools,
limits, audit and Approvals as connected ones.

The adapters speak each provider's public HTTP API directly (no extra packages): one conversation with tool calls, at most
MAX_ROUNDS rounds, every tool call through `core.call_tool`, the tokens counted against the agent's daily limit.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from ...config import settings
from ...core.crypto import decrypt
from ...core.logging import get_logger
from ...db import transaction
from ...models import AgentTask, OutsideAgent, TeamRun
from ...workers import queue as q
from ...workers.worker import task
from ..team import approvals, config, runner
from . import core

log = get_logger(__name__)

MAX_ROUNDS = 16  # tool rounds in one run (a safety rail, as for the team)
TIMEOUT = 120
PROVIDERS = ("anthropic", "openai", "google")
KEY_CONTEXT = "outside-agent-provider-key"

ToolCall = Callable[[str, dict[str, Any]], dict[str, Any]]
OnUsage = Callable[[int, int], None]


class Stop(RuntimeError):
    """The run stops (a limit reached, the agent paused); the reason is the report."""


def _schema(node: Any) -> Any:
    from .gateway import _json_schema

    return _json_schema(node)


def anthropic(key: str, model: str, system: str, prompt: str, decls: list[dict[str, Any]], call: ToolCall, on_usage: OnUsage, client: httpx.Client) -> str:
    tools = [{"name": d["name"], "description": d["description"], "input_schema": _schema(d["parameters"])} for d in decls]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    last = ""
    for _round in range(MAX_ROUNDS):
        r = client.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                        json={"model": model, "max_tokens": 4000, "system": system, "messages": messages, "tools": tools})
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        on_usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
        blocks = data.get("content") or []
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        if texts:
            last = "\n".join(texts)
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            break
        messages.append({"role": "assistant", "content": blocks})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(call(u["name"], u.get("input") or {}),
                                                                                                                        ensure_ascii=False, default=str)} for u in uses]})
    return last


def openai(key: str, model: str, system: str, prompt: str, decls: list[dict[str, Any]], call: ToolCall, on_usage: OnUsage, client: httpx.Client) -> str:
    tools = [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": _schema(d["parameters"])}} for d in decls]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    last = ""
    for _round in range(MAX_ROUNDS):
        r = client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": model, "messages": messages, "tools": tools, "max_completion_tokens": 4000})
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        on_usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
        message = (data.get("choices") or [{}])[0].get("message") or {}
        if message.get("content"):
            last = str(message["content"])
        calls = message.get("tool_calls") or []
        if not calls:
            break
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": json.dumps(call(str(fn.get("name") or ""), args if isinstance(args, dict) else {}),
                                                                                                 ensure_ascii=False, default=str)})
    return last


def google(key: str, model: str, system: str, prompt: str, decls: list[dict[str, Any]], call: ToolCall, on_usage: OnUsage, client: httpx.Client) -> str:
    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt}]}]
    last = ""
    for _round in range(MAX_ROUNDS):
        r = client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": key},
                        json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "tools": [{"functionDeclarations": decls}] if decls else []})
        r.raise_for_status()
        data = r.json()
        usage = data.get("usageMetadata") or {}
        on_usage(int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0))
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        texts = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
        if texts:
            last = "\n".join(texts)
        fcalls = [p["functionCall"] for p in parts if p.get("functionCall")]
        if not fcalls:
            break
        contents.append({"role": "model", "parts": parts})
        contents.append({"role": "user", "parts": [{"functionResponse": {"name": f["name"], "response": call(f["name"], f.get("args") or {})}} for f in fcalls]})
    return last


ADAPTERS: dict[str, Callable[..., str]] = {"anthropic": anthropic, "openai": openai, "google": google}


def provider_key(row: OutsideAgent) -> str:
    return decrypt(row.provider_key_ciphertext, associated_data=f"{KEY_CONTEXT}:{row.id}") if row.provider_key_ciphertext else ""


def _db() -> Any:
    return transaction(write=True)


def run(agent_id: int, *, trigger: str = "schedule", client: httpx.Client | None = None) -> dict[str, Any]:
    """One working session of a hosted outside agent."""
    with _db() as session:
        row = session.get(OutsideAgent, agent_id)
        if row is None or row.kind != "hosted":
            return {"skipped": "no such hosted agent"}
        if row.status in ("revoked", "paused", "draft") or core.rules(session).paused_all:
            return {"skipped": row.status}
        key = provider_key(row)
        if not key or row.provider not in ADAPTERS or not row.model:
            return {"skipped": "provider, model or key missing"}
        try:
            core.check_limits(session, row)
        except core.Refused as exc:
            return {"skipped": str(exc)}
        playbook = approvals.latest_playbook(session, row.key)
        now = datetime.now(UTC)
        run_row = TeamRun(agent=row.key, trigger=trigger, status="running", started_at=now, summary="", tool_calls=[], waiting_for=[], input_tokens=0,
                          output_tokens=0, cost_usd=0.0, error="")
        session.add(run_row)
        row.last_run_at, row.last_seen_at = now, now
        if row.status == "waiting":
            row.status = "active"
        session.flush()
        run_id = run_row.id
        session.expunge(row)
        decls = core.declarations(row)
    system = (f"You are {row.name}, an outside agent at {settings().app_name}. Your job: {row.job}\n\n{core.house_rules()}"
              + (f"\n\nTHE OWNER'S PLAYBOOK FOR YOU:\n{playbook.text}" if playbook else ""))
    prompt = ("Work now. First read my_job, my_directives and my_messages. Then do your job with your tools — propose what should change, "
              "with your reason and evidence. End with file_report: what you looked at, what you proposed, what is left.")
    used = {"in": 0, "out": 0}
    calls: list[dict[str, Any]] = []

    def on_usage(i: int, o: int) -> None:
        used["in"] += i
        used["out"] += o
        with _db() as session:
            fresh = session.get(OutsideAgent, agent_id)
            if fresh is None:
                raise Stop("The agent was removed.")
            core.record(session, fresh, via="hosted", method="model", tool=fresh.model, input_tokens=i, output_tokens=o)
            if fresh.status == "paused" or core.rules(session).paused_all:
                raise Stop("The owner paused the agent.")
            if fresh.tokens_per_day is not None:
                try:
                    core.check_limits(session, fresh)
                except core.Refused as exc:
                    raise Stop(str(exc)) from exc

    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        with _db() as session:
            fresh = session.get(OutsideAgent, agent_id)
            if fresh is None:
                raise Stop("The agent was removed.")
            try:
                core.check_limits(session, fresh)
            except core.Refused as exc:
                raise Stop(str(exc)) from exc
            session.expunge(fresh)
        started = time.perf_counter()
        result = core.call_tool(_db, fresh, name, args)
        ok = "error" not in result
        calls.append({"tool": name, "ok": ok})
        with _db() as session:
            again = session.get(OutsideAgent, agent_id)
            if again is not None:
                core.record(session, again, via="hosted", method="tools/call", tool=name, ok=ok, error=str(result.get("error", ""))[:300] if not ok else "",
                            args=args, result_chars=len(json.dumps(result, default=str)), duration_ms=int((time.perf_counter() - started) * 1000))
        return result

    status, error, text = "done", "", ""
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        text = ADAPTERS[row.provider](key, row.model, system, prompt, decls, call, on_usage, http)
    except Stop as exc:
        status, error = "skipped", str(exc)
    except httpx.HTTPStatusError as exc:
        status, error = "failed", f"The provider answered {exc.response.status_code}: {exc.response.text[:300]}"
    except Exception as exc:  # noqa: BLE001 - the owner reads what failed, never a crash of the worker
        status, error = "failed", f"{exc.__class__.__name__}: {str(exc)[:300]}"
    finally:
        if own:
            http.close()
    with _db() as session:
        run_row = session.get(TeamRun, run_id)
        if run_row is not None:
            reported = session.execute(select(TeamRun.id).where(TeamRun.agent == row.key, TeamRun.trigger == "outside", TeamRun.id > run_id)).first()
            run_row.status, run_row.finished_at, run_row.error = status, datetime.now(UTC), error
            run_row.summary = (text or error or ("Filed its report." if reported else ""))[:8000]
            run_row.tool_calls, run_row.input_tokens, run_row.output_tokens = calls[:200], used["in"], used["out"]
    return {"status": status, "calls": len(calls), "tokens": used["in"] + used["out"], "error": error}


@task("outside.run", queue="agents")
def outside_run(job: AgentTask) -> dict[str, Any]:
    return run(int(job.payload.get("agent_id") or 0), trigger=str(job.payload.get("trigger") or "schedule"))


def start(session: Any, row: OutsideAgent, trigger: str) -> bool:
    return q.enqueue(session, "outside.run", {"agent_id": row.id, "trigger": trigger}, queue="agents", max_attempts=1,
                     dedupe_key=f"outside.run:{row.id}", entity_type="outside_agent", entity_id=str(row.id)) is not None


def dispatch(session: Any, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    if core.rules(session).paused_all:
        return []
    started = []
    for row in session.execute(select(OutsideAgent).where(OutsideAgent.kind == "hosted", OutsideAgent.status.in_(("active", "waiting")))).scalars():
        try:
            schedule = config.AgentSchedule.model_validate(row.schedule or {})
        except ValueError:
            continue
        if not (schedule.times or schedule.every_minutes):
            continue
        last = row.last_run_at or row.created_at
        if runner.due(schedule, last, now) and start(session, row, "schedule"):
            started.append(row.key)
    return started


@task("outside.dispatch", queue="maintenance")
def outside_dispatch(_job: AgentTask) -> dict[str, Any]:
    with _db() as session:
        return {"started": dispatch(session)}
