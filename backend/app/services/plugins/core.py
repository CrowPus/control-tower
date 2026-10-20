"""The plugins of this install: loading them, registering their parts with the team, switching them on and off.

Every loaded plugin's parts stay registered while its code (or its HTTP manifest) is here: tools in the team's tool
catalogue as `<plugin>_<tool>`, actions as `<plugin>.<action>` with their executor and checks, metrics as Insights
numbers `<plugin>_<metric>`, starter templates as `<plugin>.<template>`. A plugin the owner has not switched on is
hidden from everything that offers a choice (the agent template, the templates) and refuses every call — so a cleared
action of a plugin switched off later fails with a clear reason instead of disappearing.

The owner's choices (on/off, the plugin's settings, its secret) are platform settings ("plugins"), HTTP connections
are "plugin_connections". Each process reads them again at most every REFRESH_SECONDS (`ensure()`), so the server and
the worker agree within seconds.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets as _secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...config import settings
from ...core import crypto
from ...core.logging import get_logger
from ...models import KpiDaily, TeamApproval
from ...plugin_api import API_MAJOR, KEY, PART, Action, Metric, Param, Plugin, Template, Tool
from .. import platform

log = get_logger(__name__)

STATE = "plugins"
CONNECTIONS = "plugin_connections"
REFRESH_SECONDS = 3.0
# Safety rails, not business values: how much one tool result may hand the model, how long an event type may be.
MAX_RESULT_CHARS = 60_000
_EVENT = re.compile(r"^[a-z][a-z0-9_.]{0,40}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
HIDDEN = "(personal — hidden)"


class PluginError(ValueError):
    """A plugin that breaks the interface; the message says how."""


@dataclass
class Loaded:
    plugin: Plugin
    source: Literal["python", "http"]
    location: str
    tools: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    kpis: tuple[str, ...] = ()
    templates: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)
    # What it was built from (HTTP: the manifest), to rebuild only when that changes.
    fingerprint: str = ""


LOADED: dict[str, Loaded] = {}
# Python plugin folders that could not be loaded, and why.
FAILED: dict[str, str] = {}
# Every registered part name → its plugin.
_OWNER: dict[str, str] = {}
_lock = threading.RLock()
_cache: dict[str, Any] = {"state": {}, "connections": {}, "read_at": 0.0, "python_loaded": False}


# --- the owner's choices ------------------------------------------------------------------------------------------
def _read(session: Session | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if session is not None:
        return dict(platform.get(session, STATE) or {}), dict(platform.get(session, CONNECTIONS) or {})
    from ...db import transaction

    try:
        with transaction() as s:
            return dict(platform.get(s, STATE) or {}), dict(platform.get(s, CONNECTIONS) or {})
    except SQLAlchemyError:  # before the first migration
        return {}, {}


def _fresh() -> tuple[dict[str, Any], dict[str, Any]]:
    if time.monotonic() - _cache["read_at"] > REFRESH_SECONDS:
        state, connections = _read()
        with _lock:
            _cache.update(state=state, connections=connections, read_at=time.monotonic())
    return _cache["state"], _cache["connections"]


def state() -> dict[str, Any]:
    return _fresh()[0]


def connections() -> dict[str, Any]:
    return _fresh()[1]


def save_state(session: Session, value: dict[str, Any]) -> None:
    platform.put(session, STATE, value)
    with _lock:
        _cache.update(state=value, read_at=time.monotonic())


def save_connections(session: Session, value: dict[str, Any]) -> None:
    platform.put(session, CONNECTIONS, value)
    with _lock:
        _cache.update(connections=value, read_at=time.monotonic())
    sync_connections(value)


def enabled(key: str) -> bool:
    return key in LOADED and bool(state().get(key, {}).get("enabled"))


def is_off(name: str) -> bool:
    """A part of a plugin that is not switched on (core parts are never off)."""
    owner = _OWNER.get(name)
    return owner is not None and not enabled(owner)


def owner_of(name: str) -> str | None:
    return _OWNER.get(name)


def values(key: str) -> dict[str, Any]:
    """The owner's settings for a plugin, secrets decrypted."""
    out: dict[str, Any] = {}
    for name, value in (state().get(key, {}).get("values") or {}).items():
        out[name] = crypto.decrypt(value["enc"], associated_data=f"plugin:{key}:{name}") if isinstance(value, dict) and "enc" in value else value
    return out


def secret(key: str) -> str:
    stored = state().get(key, {}).get("secret")
    return crypto.decrypt(stored, associated_data=f"plugin-secret:{key}") if stored else ""


def new_secret(session: Session, key: str) -> str:
    """A new shared secret for signing between Control Tower and the project; the old one stops working at once."""
    value = _secrets.token_urlsafe(32)
    current = {k: dict(v) for k, v in state().items()}
    current.setdefault(key, {})["secret"] = crypto.encrypt(value, associated_data=f"plugin-secret:{key}")
    save_state(session, current)
    return value


# --- loading ------------------------------------------------------------------------------------------------------
def ensure() -> None:
    """Load the Python plugins once and keep the HTTP connections in step with the owner's list. Call before
    reading the tool catalogue, the actions or the templates."""
    if not _cache["python_loaded"]:
        with _lock:
            if not _cache["python_loaded"]:
                from . import loader

                for found in loader.discover(Path(settings().plugins_dir)):
                    if isinstance(found, tuple):
                        FAILED[found[0]] = found[1]
                        continue
                    try:
                        register(found.plugin, "python", found.location)
                    except PluginError as exc:
                        FAILED[found.plugin.key] = str(exc)
                        log.warning("plugin %s not loaded: %s", found.plugin.key, exc)
                _cache["python_loaded"] = True
    sync_connections(connections())


def sync_connections(wanted: Mapping[str, Any]) -> None:
    from . import connector

    with _lock:
        for key in [k for k, v in LOADED.items() if v.source == "http" and (k not in wanted or not wanted[k].get("manifest"))]:
            unregister(key)
        for key, conn in wanted.items():
            manifest = conn.get("manifest")
            if not manifest:
                continue
            fingerprint = hashlib.sha256(json.dumps([conn.get("base_url"), manifest], sort_keys=True).encode()).hexdigest()
            if key in LOADED and LOADED[key].fingerprint == fingerprint:
                continue
            if key in LOADED and LOADED[key].source != "http":
                continue
            unregister(key)
            try:
                plugin = connector.build(key, str(conn["base_url"]), manifest)
                register(plugin, "http", str(conn["base_url"]), fingerprint=fingerprint)
                FAILED.pop(key, None)
            except PluginError as exc:
                FAILED[key] = str(exc)


def validate(plugin: Plugin) -> None:
    """The interface's rules for a plugin; PluginError says the first one broken."""
    if not isinstance(plugin, Plugin):
        raise PluginError("`plugin` must be a control tower Plugin(...).")
    if not KEY.match(plugin.key):
        raise PluginError(f"Plugin key {plugin.key!r}: lowercase letters, digits and _, 2–24 characters, starting with a letter.")
    try:
        major = int(str(plugin.api).split(".")[0])
    except ValueError as exc:
        raise PluginError(f"api {plugin.api!r} is not a version like '1.0'.") from exc
    if major != API_MAJOR:
        raise PluginError(f"Written for interface {plugin.api}; this Control Tower speaks {API_MAJOR}.x.")
    for kind, parts in (("tool", plugin.tools), ("action", plugin.actions), ("metric", plugin.metrics), ("template", plugin.templates),
                        ("setting", plugin.settings)):
        keys = [p.key for p in parts]
        bad = [k for k in keys if not PART.match(k)]
        if bad:
            raise PluginError(f"{kind} keys must be lowercase letters, digits and _ (2–32): {', '.join(bad)}")
        if len(set(keys)) != len(keys):
            raise PluginError(f"Two {kind}s share a key.")
    for tool in plugin.tools:
        if not callable(tool.run):
            raise PluginError(f"tool {tool.key}: run is not a function.")
        _check_params(f"tool {tool.key}", tool.params)
    for action in plugin.actions:
        if not callable(action.execute):
            raise PluginError(f"action {action.key}: execute is not a function.")
        if action.ceiling not in ("L2", "L3"):
            raise PluginError(f"action {action.key}: ceiling is L2 or L3.")
        if action.target and action.target not in action.fields:
            raise PluginError(f"action {action.key}: target {action.target!r} is not one of its fields.")
        _check_params(f"action {action.key}", action.fields)
    for metric in plugin.metrics:
        if not callable(metric.read):
            raise PluginError(f"metric {metric.key}: read is not a function.")


def _check_params(where: str, params: Mapping[str, Param]) -> None:
    for name, param in params.items():
        if not _PARAM.match(name):
            raise PluginError(f"{where}: field {name!r} must be lowercase letters, digits and _.")
        if param.type not in ("string", "integer", "number", "boolean", "array", "object"):
            raise PluginError(f"{where}: field {name} has an unknown type {param.type!r}.")


def register(plugin: Plugin, source: Literal["python", "http"], location: str, *, fingerprint: str = "") -> Loaded:
    from ..insights import queries
    from ..team import approvals, registry, templates, tools

    validate(plugin)
    with _lock:
        if plugin.key in LOADED:
            raise PluginError(f"Another plugin is already called {plugin.key!r}.")
        key = plugin.key
        tool_names = {t.key: f"{key}_{t.key}" for t in plugin.tools}
        action_names = {a.key: f"{key}.{a.key}" for a in plugin.actions}
        kpi_names = {m.key: f"{key}_{m.key}" for m in plugin.metrics}
        clash = [n for n in tool_names.values() if n in tools.CATALOGUE] + [n for n in action_names.values() if n in registry.ACTIONS] + \
                [n for n in kpi_names.values() if n in queries.BY_KEY]
        if clash:
            raise PluginError(f"These names are taken already: {', '.join(clash)}")
        personal = frozenset(p.lower() for p in plugin.personal)
        loaded = Loaded(plugin=plugin, source=source, location=location, fingerprint=fingerprint)
        for tool in plugin.tools:
            name = tool_names[tool.key]
            properties, required = declaration(tool.params)
            tools.CATALOGUE[name] = (_tool_fn(key, tool, personal), f"{tool.description} (from {plugin.name})", properties, required)
            _OWNER[name] = key
        for action in plugin.actions:
            name = action_names[action.key]
            registry.ACTIONS[name] = registry.ActionType(name, f"{action.label} ({plugin.name})", action.ceiling, payload_doc(action.fields))
            approvals.EXECUTORS[name] = _executor(key, action, personal)
            approvals.CHECKS[name] = _checker(key, action)
            if action.recheck is not None:
                approvals.RECHECKS[name] = _rechecker(key, action)
            approvals.TARGET[name] = action.target
            _OWNER[name] = key
        for metric in plugin.metrics:
            name = kpi_names[metric.key]
            spec = queries.Kpi(name, f"{metric.label} ({plugin.name})", metric_name(key, metric.key), "*", agg=metric.agg, unit=metric.unit,
                               good=metric.good, hint=metric.hint)
            queries.KPIS.append(spec)
            queries.BY_KEY[name] = spec
            _OWNER[name] = key
        made: list[str] = []
        for t in plugin.templates:
            mapped = _template(key, t, tool_names, action_names, kpi_names)
            unknown = [n for n in mapped.tools if n not in tools.CATALOGUE] + [a for a in mapped.actions if a not in registry.ACTIONS]
            if unknown:
                loaded.warnings.append(f"Template {t.key} left out: no such tool or action {', '.join(unknown)}.")
                continue
            templates.BY_KEY[mapped.key] = mapped
            _OWNER[mapped.key] = key
            made.append(mapped.key)
        loaded.tools, loaded.actions = tuple(tool_names.values()), tuple(action_names.values())
        loaded.kpis, loaded.templates = tuple(kpi_names.values()), tuple(made)
        LOADED[key] = loaded
    return loaded


def unregister(key: str) -> None:
    from ..insights import queries
    from ..team import approvals, registry, templates, tools

    with _lock:
        loaded = LOADED.pop(key, None)
        if loaded is None:
            return
        for name in loaded.tools:
            tools.CATALOGUE.pop(name, None)
        for name in loaded.actions:
            for table in (registry.ACTIONS, approvals.EXECUTORS, approvals.CHECKS, approvals.RECHECKS, approvals.TARGET):
                table.pop(name, None)
        for name in loaded.kpis:
            queries.BY_KEY.pop(name, None)
        queries.KPIS[:] = [k for k in queries.KPIS if k.key not in loaded.kpis]
        for name in loaded.templates:
            templates.BY_KEY.pop(name, None)
        for name in (*loaded.tools, *loaded.actions, *loaded.kpis, *loaded.templates):
            _OWNER.pop(name, None)


def metric_name(key: str, metric: str) -> str:
    return f"plugin.{key}.{metric}"


def _template(key: str, t: Template, tool_names: dict[str, str], action_names: dict[str, str], kpi_names: dict[str, str]) -> Any:
    from ..team import templates

    return templates.Template(f"{key}.{t.key}", t.name, t.department, t.job, t.guide, tuple(tool_names.get(x, x) for x in t.tools),
                              actions=tuple(action_names.get(x, x) for x in t.actions), subscribes=t.subscribes, emits=t.emits,
                              kpi=kpi_names.get(t.kpi, t.kpi), max_rounds=t.max_rounds, playbook=t.playbook, tags=(key,))


# --- calling a plugin ---------------------------------------------------------------------------------------------
@dataclass
class _Ctx:
    plugin: str
    settings: Mapping[str, Any]
    agent: str
    data_dir: Path
    _emit: Callable[[str, dict[str, Any]], None]

    def emit(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        self._emit(type_, payload or {})


def context(key: str, *, agent: str = "", session: Session | None = None, db: Callable[[], Any] | None = None) -> _Ctx:
    personal = frozenset(p.lower() for p in LOADED[key].plugin.personal) if key in LOADED else frozenset()

    def emit(type_: str, payload: dict[str, Any]) -> None:
        if not _EVENT.match(type_):
            raise ValueError(f"Event type {type_!r}: lowercase letters, digits, _ and dots.")
        emit_event(session, key, type_, mask(payload, personal), db=db)

    folder = Path(settings().data_dir) / "plugins" / key
    folder.mkdir(parents=True, exist_ok=True)
    return _Ctx(plugin=key, settings=values(key), agent=agent, data_dir=folder, _emit=emit)


def emit_event(session: Session | None, key: str, type_: str, payload: dict[str, Any], *, db: Callable[[], Any] | None = None) -> None:
    from ..team import events

    if session is not None:
        events.emit(session, f"{key}.{type_}", f"plugin:{key}", payload)
    elif db is not None:
        with db() as s:
            events.emit(s, f"{key}.{type_}", f"plugin:{key}", payload)
    else:
        raise RuntimeError("No database to record the event in.")


def mask(value: Any, personal: frozenset[str]) -> Any:
    """The value with every field the plugin marked personal hidden, at any depth."""
    if not personal:
        return value
    if isinstance(value, dict):
        return {k: (HIDDEN if str(k).lower() in personal else mask(v, personal)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, personal) for v in value]
    return value


def fit(result: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(result, default=str, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        decoded: dict[str, Any] = json.loads(text)
        return decoded
    return {"truncated": True, "note": "The answer was too long and was cut; ask for less (a filter or a smaller limit).",
            "start": text[:MAX_RESULT_CHARS]}


def declaration(params: Mapping[str, Param]) -> tuple[dict[str, Any], list[str]]:
    """Parameters as the model's tool declaration (an object is passed as JSON text)."""
    properties: dict[str, Any] = {}
    for name, p in params.items():
        if p.type == "object":
            properties[name] = {"type": "STRING", "description": f"{p.description} (a JSON object)".strip()}
        elif p.type == "array":
            properties[name] = {"type": "ARRAY", "items": {"type": p.items.upper() if p.items != "object" else "STRING"}, "description": p.description}
        else:
            properties[name] = {"type": p.type.upper(), "description": p.description}
    return properties, [n for n, p in params.items() if p.required]


def payload_doc(fields: Mapping[str, Param]) -> str:
    return json.dumps({name: f"{p.type}{' (required)' if p.required else ''}{' — ' + p.description if p.description else ''}"
                       for name, p in fields.items()}, ensure_ascii=False)


def clean(params: Mapping[str, Param], given: Mapping[str, Any]) -> dict[str, Any]:
    """Only the declared fields, each of its declared type; ValueError names the first that is wrong or missing."""
    out: dict[str, Any] = {}
    for name, p in params.items():
        value = given.get(name)
        if value is None or value == "" and p.type != "string":
            if p.required:
                raise ValueError(f"{name} is required.")
            continue
        if p.type == "object" and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be a JSON object.") from exc
        if p.type == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
        ok = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }[p.type]
        if not ok:
            raise ValueError(f"{name} must be {'an' if p.type[0] in 'aeio' else 'a'} {p.type}.")
        if p.required and isinstance(value, str) and not value.strip():
            raise ValueError(f"{name} is required.")
        out[name] = value
    return out


def _off(key: str) -> str:
    name = LOADED[key].plugin.name if key in LOADED else key
    return f"The {name} plugin is switched off (Admin → Plugins); nothing was done."


def _tool_fn(key: str, tool: Tool, personal: frozenset[str]) -> Callable[..., dict[str, Any]]:
    def run(ctx: Any, **args: Any) -> dict[str, Any]:
        if not enabled(key):
            return {"error": _off(key)}
        try:
            given = clean(tool.params, args)
        except ValueError as exc:
            return {"error": str(exc)}
        agent = getattr(getattr(ctx, "agent", None), "key", "") if ctx is not None else ""
        result = tool.run(context(key, agent=agent, db=getattr(ctx, "db", None)), **given)
        return fit(mask(result if isinstance(result, dict) else {"result": result}, personal))

    return run


def call_tool(session: Session, key: str, tool_key: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A plugin's tool called for the owner (its page), with personal fields hidden as for an agent."""
    loaded = LOADED[key]
    tool = next(t for t in loaded.plugin.tools if t.key == tool_key)
    if not enabled(key):
        return {"error": _off(key)}
    given = clean(tool.params, args or {})
    result = tool.run(context(key, session=session), **given)
    return fit(mask(result if isinstance(result, dict) else {"result": result}, frozenset(p.lower() for p in loaded.plugin.personal)))


def _executor(key: str, action: Action, personal: frozenset[str]) -> Callable[[Session, TeamApproval, dict[str, Any]], dict[str, Any]]:
    def execute(session: Session, approval: TeamApproval, payload: dict[str, Any]) -> dict[str, Any]:
        if not enabled(key):
            raise RuntimeError(_off(key))
        result = action.execute(context(key, agent=approval.agent, session=session), clean(action.fields, payload), approval.id)
        return fit(mask(result if isinstance(result, dict) else {"result": result}, personal))

    return execute


def _checker(key: str, action: Action) -> Callable[[Session, dict[str, Any]], list[str]]:
    def check(session: Session, payload: dict[str, Any]) -> list[str]:
        if not enabled(key):
            return [_off(key)]
        try:
            given = clean(action.fields, payload)
        except ValueError as exc:
            return [str(exc)]
        if action.check is None:
            return []
        try:
            return [str(p) for p in action.check(context(key, session=session), given)][:10]
        except Exception as exc:  # noqa: BLE001 - the project's check failing must not let the proposal through unchecked
            log.warning("plugin %s check %s failed: %s", key, action.key, exc.__class__.__name__)
            return [f"The {action.key} check of the plugin failed ({exc.__class__.__name__}); try again later."]

    return check


def _rechecker(key: str, action: Action) -> Callable[[Session, TeamApproval], list[str]]:
    def recheck(session: Session, row: TeamApproval) -> list[str]:
        if action.recheck is None or not enabled(key):
            return []
        payload = row.edited_payload if row.edited_payload is not None else row.payload
        try:
            return [str(p) for p in action.recheck(context(key, session=session), clean(action.fields, payload))][:10]
        except Exception as exc:  # noqa: BLE001 - an unreachable project never withdraws the owner's proposals
            log.warning("plugin %s recheck %s failed: %s", key, action.key, exc.__class__.__name__)
            return []

    return recheck


# --- numbers ------------------------------------------------------------------------------------------------------
def collect(session: Session, *, only: str | None = None) -> dict[str, list[str]]:
    """Today's value of every metric of every switched-on plugin into Insights; the problems per plugin."""
    from ..insights.collector import local_day

    ensure()
    day = local_day(datetime.now(UTC))
    problems: dict[str, list[str]] = {}
    for key, loaded in list(LOADED.items()):
        if (only and key != only) or not enabled(key) or not loaded.plugin.metrics:
            continue
        errors: list[str] = []
        for metric in loaded.plugin.metrics:
            try:
                rows = _metric_rows(metric, metric.read(context(key, session=session)))
            except Exception as exc:  # noqa: BLE001 - one failing number must not stop the others
                errors.append(f"{metric.key}: {exc.__class__.__name__}: {str(exc)[:200]}")
                continue
            name = metric_name(key, metric.key)
            session.execute(delete(KpiDaily).where(KpiDaily.day == day, KpiDaily.metric == name))
            for dimension, value in rows:
                session.add(KpiDaily(day=day, metric=name, dimension=dimension[:80], value=value))
        problems[key] = errors
        current = {k: dict(v) for k, v in _read(session)[0].items()}
        current.setdefault(key, {})["collected"] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "errors": errors}
        save_state(session, current)
    session.flush()
    return problems


def _metric_rows(metric: Metric, value: Any) -> list[tuple[str, float]]:
    if isinstance(value, Mapping):
        return [(str(k), float(v)) for k, v in value.items()]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{metric.key} must be a number or a mapping of numbers.")
    return [("", float(value))]
