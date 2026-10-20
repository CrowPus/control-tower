"""Admin → Plugins: the projects connected to this install. Python plugins come from the server's `plugins/` folder;
HTTP connections the owner adds here. The owner switches each on, fills in its settings, renews its secret, reads its
views and collects its numbers now. Every change is in the admin log; secrets are never shown again after they are set,
except a new shared secret once, when it is made."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..core import crypto
from ..core.errors import NotFound, ValidationFailed
from ..services.plugins import connector
from ..services.plugins import core as plugins
from ..services.team import registry, tools
from .auth import record
from .deps import AdminDb, CurrentAdmin, OwnerAdmin

router = APIRouter(prefix="/admin-api/plugins", tags=["admin-plugins"])


class PluginSettingOut(BaseModel):
    key: str
    label: str
    hint: str
    secret: bool
    required: bool
    set: bool
    # Plain settings only; a secret's value is never sent back.
    value: str | None = None


class PluginPartOut(BaseModel):
    key: str
    label: str
    detail: str = ""


class PluginOut(BaseModel):
    key: str
    name: str
    version: str
    api: str
    description: str
    source: Literal["python", "http"]
    location: str
    enabled: bool
    missing: list[str]
    secret_set: bool
    personal: list[str]
    tools: list[PluginPartOut]
    views: list[PluginPartOut]
    actions: list[PluginPartOut]
    metrics: list[PluginPartOut]
    templates: list[PluginPartOut]
    settings: list[PluginSettingOut]
    warnings: list[str]
    collected_at: str | None
    collect_errors: list[str]


class PluginProblemOut(BaseModel):
    key: str
    source: Literal["python", "http"]
    location: str
    error: str


class PluginsOut(BaseModel):
    plugins: list[PluginOut]
    problems: list[PluginProblemOut]
    events_address: str


class PluginChangeIn(BaseModel):
    enabled: bool | None = None
    # Setting key → new value; null clears it. Settings not named stay as they are.
    values: dict[str, str | None] = Field(default_factory=dict)


class ConnectionIn(BaseModel):
    key: str = Field(min_length=2, max_length=24)
    base_url: str = Field(max_length=500)
    # The project's secret if it already has one; empty: Control Tower makes one and shows it once.
    secret: str = Field(default="", max_length=200)


class SecretOut(BaseModel):
    key: str
    secret: str


class ViewOut(BaseModel):
    result: dict[str, Any]


class CollectOut(BaseModel):
    problems: dict[str, list[str]]


def _missing(key: str, stored: dict[str, Any] | None = None) -> list[str]:
    loaded = plugins.LOADED[key]
    stored = stored if stored is not None else plugins.state().get(key, {}).get("values") or {}
    out = [s.label for s in loaded.plugin.settings if s.required and stored.get(s.key) in (None, "")]
    if loaded.source == "http" and not plugins.state().get(key, {}).get("secret"):
        out.append("Shared secret")
    return out


def _out(key: str) -> PluginOut:
    loaded = plugins.LOADED[key]
    p = loaded.plugin
    mine = plugins.state().get(key, {})
    stored = mine.get("values") or {}
    collected = mine.get("collected") or {}
    return PluginOut(
        key=key, name=p.name, version=p.version, api=p.api, description=p.description, source=loaded.source, location=loaded.location,
        enabled=plugins.enabled(key), missing=_missing(key), secret_set=bool(mine.get("secret")), personal=list(p.personal),
        tools=[PluginPartOut(key=n, label=tools.CATALOGUE[n][1]) for n in loaded.tools if n in tools.CATALOGUE],
        views=[PluginPartOut(key=t.key, label=t.description) for t in p.tools if t.view and not any(x.required for x in t.params.values())],
        actions=[PluginPartOut(key=n, label=registry.ACTIONS[n].label, detail="always you" if registry.ACTIONS[n].ceiling == "L3" else "may run alone")
                 for n in loaded.actions if n in registry.ACTIONS],
        metrics=[PluginPartOut(key=n, label=m.label) for n, m in zip(loaded.kpis, p.metrics, strict=False)],
        templates=[PluginPartOut(key=n, label=t.name) for n, t in ((n, _template(n)) for n in loaded.templates) if t is not None],
        settings=[PluginSettingOut(key=s.key, label=s.label, hint=s.hint, secret=s.secret, required=s.required, set=stored.get(s.key) not in (None, ""),
                                   value=None if s.secret else (str(stored.get(s.key)) if stored.get(s.key) is not None else None)) for s in p.settings],
        warnings=loaded.warnings, collected_at=collected.get("at"), collect_errors=list(collected.get("errors") or []),
    )


def _template(name: str) -> Any:
    from ..services.team import templates

    return templates.BY_KEY.get(name)


def _list() -> PluginsOut:
    from ..config import settings

    plugins.ensure()
    conns = plugins.connections()
    problems = [PluginProblemOut(key=k, source="http" if k in conns else "python", location=str(conns.get(k, {}).get("base_url") or k), error=e)
                for k, e in sorted(plugins.FAILED.items()) if k not in plugins.LOADED]
    # Connections still waiting for their first manifest.
    problems += [PluginProblemOut(key=k, source="http", location=str(c.get("base_url")), error=str(c.get("error") or "Waiting for the project's manifest: press Fetch."))
                 for k, c in sorted(conns.items()) if not c.get("manifest") and k not in plugins.FAILED]
    base = (settings().admin_public_base_url or settings().public_base_url or "").rstrip("/")
    return PluginsOut(plugins=[_out(k) for k in sorted(plugins.LOADED)], problems=problems, events_address=f"{base}/plugin-api/v1/<plugin>/events")


def _loaded(key: str) -> plugins.Loaded:
    plugins.ensure()
    if key not in plugins.LOADED:
        raise NotFound("No such plugin.", code="PLUGIN_NOT_FOUND")
    return plugins.LOADED[key]


@router.get("", response_model=PluginsOut)
def list_plugins(_ctx: CurrentAdmin) -> PluginsOut:
    return _list()


@router.put("/{key}", response_model=PluginsOut)
def change_plugin(key: str, data: PluginChangeIn, ctx: OwnerAdmin, session: AdminDb) -> PluginsOut:
    loaded = _loaded(key)
    current = {k: dict(v) for k, v in plugins.state().items()}
    mine = current.setdefault(key, {})
    stored = dict(mine.get("values") or {})
    known = {s.key: s for s in loaded.plugin.settings}
    for name, value in data.values.items():
        spec = known.get(name)
        if spec is None:
            raise ValidationFailed(f"{loaded.plugin.name} has no setting {name!r}.", code="INVALID_PLUGIN_SETTING")
        if value is None or value == "":
            stored.pop(name, None)
        elif spec.secret:
            stored[name] = {"enc": crypto.encrypt(value[:4000], associated_data=f"plugin:{key}:{name}")}
        else:
            stored[name] = value[:4000]
    mine["values"] = stored
    if data.enabled is not None:
        mine["enabled"] = data.enabled
    if mine.get("enabled") and _missing(key, stored):
        raise ValidationFailed(f"Before switching it on, fill in: {', '.join(_missing(key, stored))}.", code="PLUGIN_SETTINGS_MISSING")
    plugins.save_state(session, current)
    record(session, ctx.admin, "plugin.changed", target_type="plugin", target_id=key, ip=ctx.ip,
           metadata={"enabled": mine.get("enabled", False), "settings_changed": sorted(data.values)})
    return _list()


@router.post("/{key}/secret", response_model=SecretOut)
def renew_secret(key: str, ctx: OwnerAdmin, session: AdminDb) -> SecretOut:
    plugins.ensure()
    if key not in plugins.LOADED and key not in plugins.connections():
        raise NotFound("No such plugin.", code="PLUGIN_NOT_FOUND")
    value = plugins.new_secret(session, key)
    record(session, ctx.admin, "plugin.secret_renewed", target_type="plugin", target_id=key, ip=ctx.ip)
    return SecretOut(key=key, secret=value)


@router.post("/connections", response_model=SecretOut)
def add_connection(data: ConnectionIn, ctx: OwnerAdmin, session: AdminDb) -> SecretOut:
    plugins.ensure()
    key = data.key.strip().lower()
    if not re.match(r"^[a-z][a-z0-9_]{1,23}$", key):
        raise ValidationFailed("The key: lowercase letters, digits and _, starting with a letter.", code="INVALID_PLUGIN_KEY")
    if key in plugins.LOADED or key in plugins.connections():
        raise ValidationFailed(f"A plugin called {key!r} is here already.", code="PLUGIN_EXISTS")
    try:
        url = connector.base_url(data.base_url)
    except plugins.PluginError as exc:
        raise ValidationFailed(str(exc), code="INVALID_PLUGIN_ADDRESS") from exc
    secret = data.secret.strip()
    if secret and len(secret) < 24:
        raise ValidationFailed("The project's secret is at least 24 characters.", code="WEAK_PLUGIN_SECRET")
    current = {k: dict(v) for k, v in plugins.connections().items()}
    current[key] = {"base_url": url, "manifest": None, "error": ""}
    plugins.save_connections(session, current)
    if secret:
        state = {k: dict(v) for k, v in plugins.state().items()}
        state.setdefault(key, {})["secret"] = crypto.encrypt(secret, associated_data=f"plugin-secret:{key}")
        plugins.save_state(session, state)
    else:
        secret = plugins.new_secret(session, key)
    record(session, ctx.admin, "plugin.connection_added", target_type="plugin", target_id=key, ip=ctx.ip, metadata={"base_url": url})
    return SecretOut(key=key, secret="" if data.secret.strip() else secret)


@router.post("/connections/{key}/fetch", response_model=PluginsOut)
def fetch_manifest(key: str, ctx: OwnerAdmin, session: AdminDb) -> PluginsOut:
    conns = {k: dict(v) for k, v in plugins.connections().items()}
    if key not in conns:
        raise NotFound("No such connection.", code="PLUGIN_NOT_FOUND")
    conn = conns[key]
    try:
        manifest = connector.fetch_manifest(key, conn["base_url"], plugins.secret(key))
    except plugins.PluginError as exc:
        conn["error"] = str(exc)
        plugins.save_connections(session, conns)
        record(session, ctx.admin, "plugin.manifest_fetched", target_type="plugin", target_id=key, ip=ctx.ip, result="failed", metadata={"error": str(exc)})
        raise ValidationFailed(str(exc), code="PLUGIN_MANIFEST_REFUSED") from exc
    conn.update(manifest=manifest, error="")
    plugins.save_connections(session, conns)
    if key in plugins.FAILED and key not in plugins.LOADED:
        raise ValidationFailed(plugins.FAILED[key], code="PLUGIN_MANIFEST_REFUSED")
    record(session, ctx.admin, "plugin.manifest_fetched", target_type="plugin", target_id=key, ip=ctx.ip,
           metadata={"version": manifest.get("version"), "tools": len(manifest.get("tools") or []), "actions": len(manifest.get("actions") or [])})
    return _list()


@router.delete("/connections/{key}", response_model=PluginsOut)
def remove_connection(key: str, ctx: OwnerAdmin, session: AdminDb) -> PluginsOut:
    conns = {k: dict(v) for k, v in plugins.connections().items()}
    if key not in conns:
        raise NotFound("No such connection.", code="PLUGIN_NOT_FOUND")
    conns.pop(key)
    plugins.save_connections(session, conns)
    state = {k: dict(v) for k, v in plugins.state().items()}
    state.pop(key, None)
    plugins.save_state(session, state)
    plugins.FAILED.pop(key, None)
    record(session, ctx.admin, "plugin.connection_removed", target_type="plugin", target_id=key, ip=ctx.ip)
    return _list()


@router.post("/{key}/views/{tool}", response_model=ViewOut)
def open_view(key: str, tool: str, ctx: OwnerAdmin, session: AdminDb) -> ViewOut:
    loaded = _loaded(key)
    if not any(t.key == tool and t.view for t in loaded.plugin.tools):
        raise NotFound("No such view.", code="PLUGIN_VIEW_NOT_FOUND")
    try:
        result = plugins.call_tool(session, key, tool)
    except Exception as exc:  # noqa: BLE001 - the project's failure is shown to the owner, never a crash
        result = {"error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}
    record(session, ctx.admin, "plugin.view_opened", target_type="plugin", target_id=f"{key}:{tool}", ip=ctx.ip)
    return ViewOut(result=result)


@router.post("/{key}/collect", response_model=CollectOut)
def collect_now(key: str, ctx: OwnerAdmin, session: AdminDb) -> CollectOut:
    _loaded(key)
    if not plugins.enabled(key):
        raise ValidationFailed("Switch the plugin on first.", code="PLUGIN_OFF")
    problems = plugins.collect(session, only=key)
    record(session, ctx.admin, "plugin.collected", target_type="plugin", target_id=key, ip=ctx.ip, metadata={"problems": problems.get(key, [])})
    return CollectOut(problems=problems)
