"""Two budgets that never mix (owner's rule, 2026-10-09): the service's AI capacity for members and companies, and the
owner's own agent team (the agents, Nia, the picture and video studio) with its budgets in Admin → Team."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete

from app.ai.client import check_budget
from app.config import settings
from app.core.errors import BudgetExceeded
from app.models import AIUsage, PlatformSetting
from app.services.team import runner
from tests.test_9_team import AGENT, _db, _ready


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings(), "ai_daily_budget_usd", 5.0)
    monkeypatch.setattr(settings(), "ai_monthly_budget_usd", 60.0)
    yield
    with _db() as db:
        db.execute(delete(AIUsage))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))
    runner.use_model(runner.ProviderModel())


def _spent(agent_type: str, usd: float, operation: str) -> None:
    with _db() as db:
        db.add(AIUsage(agent_type=agent_type, model="m", operation=operation, estimated_cost_usd=usd, created_at=datetime.now(UTC)))


def test_the_installs_cap_counts_all_ai_use_and_stops_the_next_call() -> None:
    _spent("team", 3.20, "marketing.video")
    check_budget()  # $3.20 of the $5 test cap: still allowed
    _spent("team", 2.08, "agent.qa_runner.run")
    with pytest.raises(BudgetExceeded):
        check_budget()


class Model:
    def converse(self, *, call_tool: Callable[..., Any], before_round: Callable[[], None], on_usage: Callable[[runner.Usage], None], **_: Any) -> str:
        before_round()
        on_usage(runner.Usage("fake", 10, 10, 1))
        return "Done."


def test_busy_members_never_stop_the_agents() -> None:
    _ready()
    _spent("assistant", 9.0, "assistant.chat")  # the members' capacity is used up
    runner.use_model(Model())
    outcome = runner.run(_db, AGENT, trigger="manual")
    assert outcome.status == "done", outcome.summary
