"""The plugin interface (docs/PLUGINS.md): what a project gives Control Tower, and what Control Tower gives it back.

A plugin is a folder in `plugins/` with a `plugin.py` that defines `plugin = Plugin(...)`. It may add:

- **tools** — read tools over the project's data that agents may be given (Records);
- **actions** — kinds of proposals agents may make, carried out by the plugin only after the owner clears them;
- **metrics** — the project's numbers, collected into Insights every quarter hour (Signals);
- **templates** — starter agents that suit the project;
- **settings** — what the owner fills in on Admin → Plugins (addresses, keys; secrets are stored encrypted).

The project may also send events (`POST /plugin-api/v1/<plugin>/events`, signed) that wake the agents subscribed to them.

Everything here is the interface's version API_VERSION. A plugin names the version it was written for; a core of the
same major version runs it unchanged. Nothing in this module may change in a way that breaks a plugin of the same
major version.

The rules hold for plugins as for everything else: an action is never carried out before the owner (or, for an L2
action the owner let run alone, the owner's limit) allows it; the fields a plugin marks personal never reach an agent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

API_VERSION = "1.0"
API_MAJOR = 1

KEY = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
PART = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]


class Context(Protocol):
    """What a plugin's code receives on every call."""

    plugin: str
    # The owner's settings for this plugin (secrets decrypted). Read-only.
    settings: Mapping[str, Any]
    # The agent that called the tool or proposed the action; "" for metrics.
    agent: str
    # A folder only this plugin writes to (created on first use).
    data_dir: Path

    def emit(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        """Tell the team something happened: event `<plugin>.<type_>`. Personal fields are removed first."""


@dataclass(frozen=True)
class Param:
    type: ParamType
    description: str = ""
    required: bool = False
    # For arrays: the type of the items.
    items: ParamType = "string"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    hint: str = ""
    secret: bool = False
    required: bool = False
    # A long value (a JSON list, a text): edited in a box of several lines.
    long: bool = False


@dataclass(frozen=True)
class Tool:
    """A read tool: an agent calls it with `params` and gets the returned JSON-able dict."""

    key: str
    description: str
    run: Callable[..., dict[str, Any]]  # run(ctx, **params) -> dict
    params: Mapping[str, Param] = field(default_factory=dict)
    # Shown to the owner as a table on the plugin's page (only for tools without required params).
    view: bool = False


@dataclass(frozen=True)
class Action:
    """A kind of proposal. The agent fills `fields`; the core checks the types, then `check`; the owner clears it;
    then `execute` carries it out once (the approval id is passed, so the project can refuse a repeat)."""

    key: str
    label: str
    execute: Callable[[Context, dict[str, Any], int], dict[str, Any]]  # execute(ctx, payload, approval_id) -> result
    fields: Mapping[str, Param] = field(default_factory=dict)
    # L3: always the owner (money, deleting, anything about people's accounts). L2: the owner may let it run alone
    # within a daily limit after enough clearances. When unsure, L3.
    ceiling: Literal["L2", "L3"] = "L3"
    # Problems that make the proposal pointless (a duplicate, a record that does not exist); the agent hears them.
    check: Callable[[Context, dict[str, Any]], list[str]] | None = None
    # Checked again on every waiting proposal each minute: problems withdraw it (it became a duplicate meanwhile).
    recheck: Callable[[Context, dict[str, Any]], list[str]] | None = None
    # The field naming what the proposal is about: a second proposal for the same value while the first waits is
    # refused. "": one waiting proposal of this kind per agent.
    target: str = ""


@dataclass(frozen=True)
class Metric:
    """A number for Insights: `read` returns today's value, or one value per dimension (a plan, a country)."""

    key: str
    label: str
    read: Callable[[Context], float | Mapping[str, float]]
    unit: Literal["count", "usd", "ms", "hours", "percent"] = "count"
    good: Literal["up", "down", "neutral"] = "up"
    # How days add up over a period: sum (signups), last (users in total), avg, max.
    agg: Literal["sum", "avg", "last", "max"] = "sum"
    hint: str = ""


@dataclass(frozen=True)
class Template:
    """A starter agent. Tools and actions may name the plugin's own by their short key, or any core tool."""

    key: str
    name: str
    department: str
    job: str
    guide: str
    tools: tuple[str, ...]
    actions: tuple[str, ...] = ()
    subscribes: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    kpi: str = ""
    max_rounds: int = 0
    playbook: str = ""


@dataclass(frozen=True)
class Plugin:
    key: str
    name: str
    version: str
    # The interface version it was written for ("1.0").
    api: str
    description: str = ""
    tools: tuple[Tool, ...] = ()
    actions: tuple[Action, ...] = ()
    metrics: tuple[Metric, ...] = ()
    templates: tuple[Template, ...] = ()
    settings: tuple[Setting, ...] = ()
    # Field names that hold personal data (email, phone, name …): removed from every tool result and event before an
    # agent sees it, wherever they occur.
    personal: tuple[str, ...] = ()
    # For a plugin whose parts depend on the owner's settings (a database plugin's named queries): called with the
    # settings (secrets decrypted) when the plugin loads and whenever its settings change; returns the Plugin to use,
    # with the same key. Raising ValueError keeps the plugin's fixed parts and shows the message on Admin → Plugins.
    build: Callable[[Mapping[str, Any]], Plugin] | None = None
