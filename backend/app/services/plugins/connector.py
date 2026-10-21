"""The HTTP connector: a project in any language, on any server, speaks the plugin interface over HTTPS
(docs/PLUGINS.md → HTTP). The owner adds its address on Admin → Plugins; Control Tower reads its manifest and builds
the same Plugin a Python plugin would define, whose parts call the project:

    GET  <base>/manifest                     what the project offers (tools, actions, metrics, templates, personal fields)
    POST <base>/tools/<tool>                 {"args": {...}, "agent": "<key>"}            → any JSON object
    POST <base>/actions/<action>/check       {"payload": {...}}                            → {"problems": [...]}
    POST <base>/actions/<action>/recheck     {"payload": {...}}                            → {"problems": [...]}
    POST <base>/actions/<action>/execute     {"payload": {...}, "approval_id": 12}        → any JSON object
    GET  <base>/metrics?since=<ISO time>     → {"values": {"<metric>": 12, "<metric>": {"<dimension>": 3}}}  (today = since then)

Every request is signed with the connection's secret (signing.py); the project must refuse an unsigned or stale one.
`execute` is called once per cleared proposal; the approval id lets the project refuse a repeat.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError

from ...plugin_api import Action, Metric, Param, Plugin, Template, Tool
from . import core, signing

# Tests replace the transport; None = the network.
TRANSPORT: httpx.BaseTransport | None = None
# Safety rails, not business values.
TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 2_000_000
METRICS_CACHE_SECONDS = 30.0

ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]


class _Param(BaseModel):
    type: ParamType
    description: str = Field(default="", max_length=500)
    required: bool = False
    items: ParamType = "string"


class _Tool(BaseModel):
    key: str
    description: str = Field(max_length=1000)
    params: dict[str, _Param] = Field(default_factory=dict)
    view: bool = False


class _Action(BaseModel):
    key: str
    label: str = Field(max_length=200)
    ceiling: Literal["L2", "L3"] = "L3"
    fields: dict[str, _Param] = Field(default_factory=dict)
    target: str = ""
    check: bool = False
    recheck: bool = False


class _Metric(BaseModel):
    key: str
    label: str = Field(max_length=100)
    unit: Literal["count", "usd", "ms", "hours", "percent"] = "count"
    good: Literal["up", "down", "neutral"] = "up"
    agg: Literal["sum", "avg", "last", "max"] = "sum"
    hint: str = Field(default="", max_length=300)


class _Template(BaseModel):
    key: str
    name: str = Field(max_length=60)
    department: str = Field(max_length=40)
    job: str = Field(max_length=2000)
    guide: str = Field(default="", max_length=6000)
    tools: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    subscribes: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    kpi: str = ""
    max_rounds: int = 0
    playbook: str = Field(default="", max_length=20000)


class Manifest(BaseModel):
    api: str
    key: str
    name: str = Field(max_length=60)
    version: str = Field(max_length=40)
    description: str = Field(default="", max_length=1000)
    tools: list[_Tool] = Field(default_factory=list, max_length=50)
    actions: list[_Action] = Field(default_factory=list, max_length=30)
    metrics: list[_Metric] = Field(default_factory=list, max_length=30)
    templates: list[_Template] = Field(default_factory=list, max_length=20)
    personal: list[str] = Field(default_factory=list, max_length=100)


def _day_start() -> str:
    """The start of the owner's day (their time zone), as UTC ISO 8601: what "today" means for the project's numbers."""
    from datetime import UTC, datetime

    from ...core.clock import zone

    local = datetime.now(zone()).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC).isoformat().replace("+00:00", "Z")


def base_url(value: str) -> str:
    """The project's address for the connector, checked: http(s), a host, no password, query or fragment."""
    url = value.strip().rstrip("/")
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise core.PluginError("The address starts with https:// (or http:// on your own machine) and names a host.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise core.PluginError("The address has no password, ? or # in it.")
    return url


def _request(method: str, url: str, secret: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.dumps(body).encode() if body is not None else b""
    headers = {signing.HEADER: signing.sign(secret, urlparse(url).path, raw), "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=TRANSPORT, follow_redirects=False) as client:
            response = client.request(method, url, content=raw or None, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"The project did not answer ({exc.__class__.__name__}).") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise RuntimeError("The project's answer was too large.")
    try:
        data = response.json() if response.content else {}
    except ValueError as exc:
        raise RuntimeError(f"The project answered {response.status_code} without JSON.") from exc
    if response.status_code >= 400:
        reason = data.get("error") if isinstance(data, dict) else None
        raise RuntimeError(f"The project answered {response.status_code}: {str(reason or 'no reason given')[:300]}")
    if not isinstance(data, dict):
        raise RuntimeError("The project's answer was not a JSON object.")
    return data


def fetch_manifest(key: str, url: str, secret: str) -> dict[str, Any]:
    """The project's manifest, checked; PluginError says what is wrong."""
    try:
        data = _request("GET", f"{url}/manifest", secret)
    except RuntimeError as exc:
        raise core.PluginError(str(exc)) from exc
    try:
        manifest = Manifest.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise core.PluginError(f"The manifest is not right at {'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc
    if manifest.key != key:
        raise core.PluginError(f"The manifest says its key is {manifest.key!r}; this connection is {key!r}.")
    core.validate(build(key, url, manifest.model_dump()))
    return manifest.model_dump()


@dataclass
class Diagnosis:
    """What "Check the connection" found, for someone who is not a developer: the step that failed, what it means,
    and what to do."""

    ok: bool
    step: Literal["secret", "reach", "address", "routes", "unsigned", "signature", "manifest", "ok"]
    message: str
    hint: str = ""
    manifest: dict[str, Any] | None = None


def diagnose(key: str, url: str, secret: str) -> Diagnosis:
    """Ask the project for its manifest the way Control Tower will, and explain the first thing that is wrong."""
    if not secret:
        return Diagnosis(False, "secret", "Control Tower has no shared secret for this connection.",
                         "Press New secret, then put it in your project's settings as CONTROL_TOWER_SECRET.")
    target = f"{url}/manifest"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=TRANSPORT, follow_redirects=False) as client:
            unsigned = client.get(target, headers={"Accept": "application/json"})
            signed = client.get(target, headers={"Accept": "application/json", signing.HEADER: signing.sign(secret, urlparse(target).path, b"")})
    except httpx.TimeoutException:
        return Diagnosis(False, "reach", f"Your project did not answer at {url} within {int(TIMEOUT_SECONDS)} seconds.",
                         "Is it running? Is the address right? A project on your own computer cannot be reached from this server.")
    except httpx.HTTPError:
        return Diagnosis(False, "reach", f"Control Tower cannot reach {url}.",
                         "Check the address (https://, spelling, the path). Is your project deployed and running? Open the address followed by "
                         "/manifest in your browser: a page saying \"refused\" means the address is right.")
    if unsigned.is_redirect:
        return Diagnosis(False, "address", f"{url} sends visitors on to {unsigned.headers.get('location', 'another address')}.",
                         "Use the final address instead (often https:// instead of http://, or with or without www).")
    if unsigned.status_code in (404, 405):
        return Diagnosis(False, "routes", f"Your project answers, but has nothing at {target}.",
                         "The Control Tower part is not deployed yet, CONTROL_TOWER_SECRET is not set in your project (it switches the part "
                         "on), or the part lives under another path than this address.")
    if unsigned.status_code >= 500:
        return Diagnosis(False, "routes", f"Your project failed when asked ({unsigned.status_code}).", "Look at your project's logs for the error.")
    if unsigned.status_code < 300:
        return Diagnosis(False, "unsigned", "Your project answers without checking who is asking.",
                         "It must refuse requests without Control Tower's signature (CONNECT-A-PROJECT.md → Signing). Ask your coding agent to add the check.")
    if signed.status_code == 401:
        return Diagnosis(False, "signature", "Your project refuses Control Tower's signature: the shared secrets are not the same.",
                         "Put the secret again into your project's CONTROL_TOWER_SECRET and restart it — or press New secret and use that one. "
                         "If the secret surely matches, a proxy may change the address your project sees.")
    if signed.status_code != 200:
        return Diagnosis(False, "routes", f"Your project answered {signed.status_code} to Control Tower's signed request.", "Look at your project's logs for the error.")
    try:
        return Diagnosis(True, "ok", "Connected.", manifest=fetch_manifest(key, url, secret))
    except core.PluginError as exc:
        return Diagnosis(False, "manifest", f"Your project answers, but what it offers is not right: {exc}",
                         "Give this sentence to your coding agent; the manifest's rules are in CONNECT-A-PROJECT.md.")


def build(key: str, url: str, raw: dict[str, Any]) -> Plugin:
    """The Plugin whose parts call the project."""
    m = Manifest.model_validate(raw)

    def params(spec: dict[str, _Param]) -> dict[str, Param]:
        return {n: Param(type=p.type, description=p.description, required=p.required, items=p.items) for n, p in spec.items()}

    def tool(t: _Tool) -> Tool:
        def run(ctx: Any, **args: Any) -> dict[str, Any]:
            return _request("POST", f"{url}/tools/{t.key}", core.secret(key), {"args": args, "agent": ctx.agent})

        return Tool(key=t.key, description=t.description, run=run, params=params(t.params), view=t.view)

    def action(a: _Action) -> Action:
        def execute(_ctx: Any, payload: dict[str, Any], approval_id: int) -> dict[str, Any]:
            return _request("POST", f"{url}/actions/{a.key}/execute", core.secret(key), {"payload": payload, "approval_id": approval_id})

        def problems(step: str) -> Any:
            def ask(_ctx: Any, payload: dict[str, Any]) -> list[str]:
                answer = _request("POST", f"{url}/actions/{a.key}/{step}", core.secret(key), {"payload": payload})
                return [str(p) for p in answer.get("problems") or []]

            return ask

        return Action(key=a.key, label=a.label, execute=execute, fields=params(a.fields), ceiling=a.ceiling,
                      check=problems("check") if a.check else None, recheck=problems("recheck") if a.recheck else None, target=a.target)

    cache: dict[str, Any] = {"at": 0.0, "values": {}}

    def metric(x: _Metric) -> Metric:
        def read(_ctx: Any) -> Any:
            if time.monotonic() - cache["at"] > METRICS_CACHE_SECONDS:
                cache["values"] = _request("GET", f"{url}/metrics?since={quote(_day_start())}", core.secret(key)).get("values") or {}
                cache["at"] = time.monotonic()
            if x.key not in cache["values"]:
                raise ValueError("the project's /metrics has no value for it")
            return cache["values"][x.key]

        return Metric(key=x.key, label=x.label, read=read, unit=x.unit, good=x.good, agg=x.agg, hint=x.hint)

    return Plugin(
        key=key, name=m.name, version=m.version, api=m.api, description=m.description,
        tools=tuple(tool(t) for t in m.tools), actions=tuple(action(a) for a in m.actions), metrics=tuple(metric(x) for x in m.metrics),
        templates=tuple(Template(key=t.key, name=t.name, department=t.department, job=t.job, guide=t.guide, tools=tuple(t.tools),
                                 actions=tuple(t.actions), subscribes=tuple(t.subscribes), emits=tuple(t.emits), kpi=t.kpi,
                                 max_rounds=t.max_rounds, playbook=t.playbook) for t in m.templates),
        settings=(), personal=tuple(m.personal),
    )
