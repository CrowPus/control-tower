"""The HTTP connector: a project in any language, on any server, speaks the plugin interface over HTTPS
(docs/PLUGINS.md → HTTP). The owner adds its address on Admin → Plugins; Control Tower reads its manifest and builds
the same Plugin a Python plugin would define, whose parts call the project:

    GET  <base>/manifest                     what the project offers (tools, actions, metrics, templates, personal fields)
    POST <base>/tools/<tool>                 {"args": {...}, "agent": "<key>"}            → any JSON object
    POST <base>/actions/<action>/check       {"payload": {...}}                            → {"problems": [...]}
    POST <base>/actions/<action>/recheck     {"payload": {...}}                            → {"problems": [...]}
    POST <base>/actions/<action>/execute     {"payload": {...}, "approval_id": 12}        → any JSON object
    GET  <base>/metrics                      → {"values": {"<metric>": 12, "<metric>": {"<dimension>": 3}}}

Every request is signed with the connection's secret (signing.py); the project must refuse an unsigned or stale one.
`execute` is called once per cleared proposal; the approval id lets the project refuse a repeat.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal
from urllib.parse import urlparse

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
                cache["values"] = _request("GET", f"{url}/metrics", core.secret(key)).get("values") or {}
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
