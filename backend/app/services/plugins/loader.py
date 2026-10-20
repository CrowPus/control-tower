"""Python plugins: each folder of `PLUGINS_DIR` with a `plugin.py` defining `plugin = Plugin(...)`.

The folder is imported as its own package (`ct_plugin_<folder>`), so a plugin may split its code into more files and
import them relatively (`from .client import Shop`). A folder whose code fails to import, or whose `plugin` breaks the
interface, is reported on Admin → Plugins and left out; the rest still load.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from ...core.logging import get_logger
from ...plugin_api import KEY, Plugin

log = get_logger(__name__)


@dataclass(frozen=True)
class Found:
    plugin: Plugin
    location: str


def discover(folder: Path) -> list[Found | tuple[str, str]]:
    """Every plugin in the folder, or (folder name, why it was left out)."""
    if not folder.is_dir():
        return []
    out: list[Found | tuple[str, str]] = []
    for path in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        entry = path / "plugin.py"
        if not entry.is_file():
            continue
        if not KEY.match(path.name):
            out.append((path.name, "The folder name is the plugin's key: lowercase letters, digits and _, 2–24 characters."))
            continue
        name = f"ct_plugin_{path.name}"
        try:
            spec = importlib.util.spec_from_file_location(name, entry, submodule_search_locations=[str(path)])
            if spec is None or spec.loader is None:
                raise ImportError("cannot be imported")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - one broken plugin must not stop the install
            sys.modules.pop(name, None)
            log.warning("plugin folder %s failed to import: %s", path.name, exc.__class__.__name__)
            out.append((path.name, f"Its code failed to load: {exc.__class__.__name__}: {str(exc)[:300]}"))
            continue
        plugin = getattr(module, "plugin", None)
        if not isinstance(plugin, Plugin):
            out.append((path.name, "plugin.py must define `plugin = Plugin(...)` (from app.plugin_api)."))
            continue
        if plugin.key != path.name:
            out.append((path.name, f"The plugin's key {plugin.key!r} must be its folder's name."))
            continue
        out.append(Found(plugin=plugin, location=str(path)))
    return out
