"""The plugins' numbers into Insights, every quarter hour (no AI): today's value of every metric of every switched-on
plugin, replaced each time, so the day ends with its last value."""

from __future__ import annotations

from typing import Any

from ...db import transaction
from ...models import AgentTask
from ...workers.worker import task
from . import core


@task("plugins.collect", queue="maintenance")
def collect(_task: AgentTask) -> dict[str, Any]:
    with transaction(write=True) as session:
        return {"problems": core.collect(session)}
