"""Anthropic Claude and OpenAI (or a service speaking its API) for the install's text work, over plain HTTPS.

Two things each provider does here, the same way the Gemini path does them:

- `converse` — the agents' tool loop (services/team/runner.py's Model): rounds of tool calls within the run's limit,
  the budget checked before every model call, an empty answer asked again, the last round only for recording what is
  done, and a summary asked for when the rounds end without one;
- `generate` — one structured answer (ai/client.py), a JSON object checked against its schema by the caller.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from .client import AICallResult

from . import providers
from .providers import ProviderError

# Safety rails, not business values.
TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_TOKENS = 4000
SUMMARY_PROMPT = "(Your tool rounds are used up. Now write your summary for the owner, from what your tools returned.)"
TRANSIENT = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Tests replace the transport; None = the network.
TRANSPORT: httpx.BaseTransport | None = None


def json_schema(node: Any) -> Any:
    """The team's tool declarations (Gemini's upper-case types) as JSON Schema."""
    if isinstance(node, dict):
        out = {k: json_schema(v) for k, v in node.items()}
        if isinstance(node.get("type"), str):
            out["type"] = node["type"].lower()
        return out
    if isinstance(node, list):
        return [json_schema(v) for v in node]
    return node


def _post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(2):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=TRANSPORT) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            if attempt == 0:
                continue
            raise ProviderError(504, "timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(503, exc.__class__.__name__) from exc
        if response.status_code < 400:
            data: dict[str, Any] = response.json()
            return data
        if response.status_code in TRANSIENT and attempt == 0:
            time.sleep(2.0)
            continue
        try:
            detail = response.json().get("error")
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = response.text[:200]
        raise ProviderError(response.status_code, str(message or "error"))
    raise ProviderError(503, "no answer")


# --- Anthropic ----------------------------------------------------------------------------------------------------
class _Anthropic:
    def __init__(self, model: str, system: str, declarations: list[dict[str, Any]]) -> None:
        self.model, self.system = model, system
        self.tools = [{"name": d["name"], "description": d["description"], "input_schema": json_schema(d["parameters"])} for d in declarations]
        self.messages: list[dict[str, Any]] = []

    def user(self, text: str, media: list[tuple[str, bytes]] | None = None) -> None:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for mime, data in media or []:
            kind = "document" if mime == "application/pdf" else "image"
            content.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}})
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] = [*self.messages[-1]["content"], *content]
        else:
            self.messages.append({"role": "user", "content": content})

    def send(self, *, tools: bool = True) -> tuple[str, list[tuple[str, str, dict[str, Any]]], int, int]:
        body: dict[str, Any] = {"model": self.model, "max_tokens": MAX_OUTPUT_TOKENS, "system": self.system, "messages": self.messages, "temperature": 0.3}
        if self.tools:
            body["tools"] = self.tools
            if not tools:
                body["tool_choice"] = {"type": "none"}
        data = _post("https://api.anthropic.com/v1/messages", {"x-api-key": providers.key("anthropic"), "anthropic-version": "2023-06-01"}, body)
        blocks = data.get("content") or []
        usage = data.get("usage") or {}
        calls = [(str(b.get("id")), str(b.get("name")), dict(b.get("input") or {})) for b in blocks if b.get("type") == "tool_use"]
        if blocks:
            self.messages.append({"role": "assistant", "content": blocks})
        text = "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text").strip()
        return text, calls, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)

    def results(self, answers: list[tuple[str, str, dict[str, Any]]], note: str = "") -> None:
        content: list[dict[str, Any]] = [{"type": "tool_result", "tool_use_id": cid, "content": json.dumps(result, ensure_ascii=False, default=str)}
                                         for cid, _name, result in answers]
        if note:
            content.append({"type": "text", "text": note})
        self.messages.append({"role": "user", "content": content})


# --- OpenAI -------------------------------------------------------------------------------------------------------
class _OpenAI:
    def __init__(self, model: str, system: str, declarations: list[dict[str, Any]]) -> None:
        self.model = model
        self.tools = [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": json_schema(d["parameters"])}}
                      for d in declarations]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def user(self, text: str, media: list[tuple[str, bytes]] | None = None) -> None:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for mime, data in media or []:
            url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            content.append({"type": "file", "file": {"filename": "attachment.pdf", "file_data": url}} if mime == "application/pdf"
                           else {"type": "image_url", "image_url": {"url": url}})
        self.messages.append({"role": "user", "content": content})

    def send(self, *, tools: bool = True) -> tuple[str, list[tuple[str, str, dict[str, Any]]], int, int]:
        body: dict[str, Any] = {"model": self.model, "messages": self.messages, "max_completion_tokens": MAX_OUTPUT_TOKENS}
        if self.tools:
            body["tools"] = self.tools
            body["tool_choice"] = "auto" if tools else "none"
        data = _post(f"{providers.openai_base_url()}/chat/completions", {"Authorization": f"Bearer {providers.key('openai')}"}, body)
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        usage = data.get("usage") or {}
        raw_calls = message.get("tool_calls") or []
        calls: list[tuple[str, str, dict[str, Any]]] = []
        for c in raw_calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append((str(c.get("id")), str(fn.get("name") or ""), args if isinstance(args, dict) else {}))
        self.messages.append({"role": "assistant", "content": message.get("content") or "", **({"tool_calls": raw_calls} if raw_calls else {})})
        return str(message.get("content") or "").strip(), calls, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

    def results(self, answers: list[tuple[str, str, dict[str, Any]]], note: str = "") -> None:
        for cid, _name, result in answers:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(result, ensure_ascii=False, default=str)})
        if note:
            self.messages.append({"role": "user", "content": note})


CHATS: dict[str, type[_Anthropic] | type[_OpenAI]] = {"anthropic": _Anthropic, "openai": _OpenAI}


class VendorModel:
    """The runner's Model for Claude or OpenAI (the same rules as GeminiModel)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def converse(self, *, tier: str, system: str, prompt: str, declarations: list[dict[str, Any]],
                 call_tool: Callable[[str, dict[str, Any]], dict[str, Any]], before_round: Callable[[], None],
                 on_usage: Callable[[Any], None], media: list[tuple[str, bytes]] | None = None, max_rounds: int | None = None) -> str:
        from ..services.team import runner

        if not providers.key(self.name):  # type: ignore[arg-type]
            raise runner.BudgetStop(f"No {providers.LABELS[self.name]} key is set (Admin → Platform → AI).")
        model = providers.model("fast" if tier == "fast" else "reasoning", self.name)  # type: ignore[arg-type]
        if not model:
            raise runner.BudgetStop(f"No {providers.LABELS[self.name]} model is named (Admin → Platform → AI).")
        chat = CHATS[self.name](model, system, declarations)
        chat.user(prompt, media)
        rounds = max_rounds or runner.MAX_ROUNDS
        last_text = ""
        for round_ in range(rounds + 1):
            text, calls = "", list[tuple[str, str, dict[str, Any]]]()
            for attempt in range(runner.EMPTY_RETRIES + 1):
                before_round()
                started = time.perf_counter()
                text, calls, tokens_in, tokens_out = chat.send()
                on_usage(runner.Usage(model, tokens_in, tokens_out, int((time.perf_counter() - started) * 1000)))
                if text or calls:
                    break
                if attempt < runner.EMPTY_RETRIES:
                    chat.user(runner.EMPTY_NUDGE)
            if text:
                last_text = text
            if not calls:
                break
            calls = calls[:runner.MAX_CALLS_PER_ROUND]
            if round_ == rounds:
                # The last round could only record what is done: carry those calls out, nothing else.
                chat.results([(cid, name, call_tool(name, args) if name in runner.FINAL_ROUND_TOOLS else {"error": "Not in the last round."})
                              for cid, name, args in calls])
                break
            answers = [(cid, name, call_tool(name, args)) for cid, name, args in calls]
            chat.results(answers, runner.LAST_ROUND if round_ == rounds - 1 else "")
        if not last_text.strip():
            before_round()
            chat.user(SUMMARY_PROMPT)
            text, _calls, tokens_in, tokens_out = chat.send(tools=False)
            on_usage(runner.Usage(model, tokens_in, tokens_out, 0))
            last_text = text
        return last_text


# --- structured answers -------------------------------------------------------------------------------------------
class VendorProvider:
    """ai/client.py's provider for Claude or OpenAI: one answer, a JSON object when a schema is given."""

    def __init__(self, name: str) -> None:
        self.name = name

    def generate(self, *, model: str, system: str, prompt: str, schema: type[BaseModel] | None, temperature: float, max_output_tokens: int) -> AICallResult:
        from .client import AICallResult

        if schema is not None:
            system = (f"{system}\n\nAnswer with one JSON object only — no text before or after it, no code fence — that matches this "
                      f"JSON schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}")
        started = time.perf_counter()
        if self.name == "anthropic":
            data = _post("https://api.anthropic.com/v1/messages", {"x-api-key": providers.key("anthropic"), "anthropic-version": "2023-06-01"},
                         {"model": model, "max_tokens": max_output_tokens, "system": system, "temperature": temperature,
                          "messages": [{"role": "user", "content": prompt}]})
            text = "\n".join(str(b.get("text") or "") for b in data.get("content") or [] if b.get("type") == "text")
            usage = data.get("usage") or {}
            tokens_in, tokens_out = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        else:
            body: dict[str, Any] = {"model": model, "max_completion_tokens": max_output_tokens,
                                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            if schema is not None:
                body["response_format"] = {"type": "json_object"}
            data = _post(f"{providers.openai_base_url()}/chat/completions", {"Authorization": f"Bearer {providers.key('openai')}"}, body)
            text = str((((data.get("choices") or [{}])[0].get("message")) or {}).get("content") or "")
            usage = data.get("usage") or {}
            tokens_in, tokens_out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        return AICallResult(text=text, model=model, input_tokens=tokens_in, output_tokens=tokens_out, latency_ms=int((time.perf_counter() - started) * 1000))
