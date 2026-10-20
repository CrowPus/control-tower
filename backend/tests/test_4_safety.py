"""Kill switches, retention and the state machines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AutomationPaused
from app.db import transaction
from app.models import LoginAttempt
from app.services import platform
from app.workers.worker import check_switches


def test_platform_kill_switches_gate_the_worker() -> None:
    with transaction(write=True) as session:
        platform.put(session, "switches", {"paused_all": True})
    try:
        with pytest.raises(AutomationPaused):
            check_switches("matching")
    finally:
        with transaction(write=True) as session:
            platform.put(session, "switches", {"paused_all": False})
    check_switches("matching")


def test_old_sign_in_attempts_are_purged_and_recent_ones_kept() -> None:
    from app.models import AgentTask
    from app.workers.handlers import maintenance_requeue_stale

    now = datetime.now(UTC)
    with transaction(write=True) as db:
        db.add(LoginAttempt(key="198.51.100.7", attempted_at=now - timedelta(days=5), success=False))
        db.add(LoginAttempt(key="198.51.100.8", attempted_at=now - timedelta(minutes=5), success=False))
    result = maintenance_requeue_stale(AgentTask(task_type="maintenance.requeue_stale", payload={}))
    assert result["login_attempts_purged"] >= 1
    with transaction() as db:
        keys = set(db.scalars(select(LoginAttempt.key)))
    assert "198.51.100.8" in keys and "198.51.100.7" not in keys
