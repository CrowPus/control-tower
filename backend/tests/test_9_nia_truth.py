"""Nia says only what really happened (2026-10-10): the owner found that changes Nia "made" and agents she "told" never
happened — four admin changes had failed when approved, a directive still waited for the owner's OK, and voice turns kept
no record of her tools."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete

from app.models import OwnerDirective, PlatformSetting, TeamApproval
from app.services.nia import memory, truth
from app.services.nia.voice import Turn, carry
from app.services.team import admin_changes, registry, tools
from tests.test_9_team import (  # noqa: F401 - the owner signed in to the admin app (fixture)
    _db,
    admin,
)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        db.execute(delete(TeamApproval))
        db.execute(delete(OwnerDirective))
        db.execute(delete(PlatformSetting).where(PlatformSetting.key == "agents"))


def test_an_admin_change_the_admin_would_refuse_is_never_proposed() -> None:
    # A body the admin would refuse, and a PUT that would reset other settings, are never proposed.
    with pytest.raises(admin_changes.Refused, match="would refuse this body"):
        admin_changes.check({"method": "PUT", "path": "/admin-api/product/checks/settings", "body": {"rules": {"every_minutes": "weekly"}, "pages": []}})
    with pytest.raises(admin_changes.Refused, match="rules: Field required"):
        admin_changes.check({"method": "PUT", "path": "/admin-api/product/checks/settings", "body": {"pages": []}})
    whole = {"rules": {"every_minutes": 30, "fail_after": 2, "slow_ms": None, "tls_warn_days": 14}, "pages": [{"name": "Shop", "url": "https://shop.example/", "contains": ""}]}
    assert admin_changes.check({"method": "PUT", "path": "/admin-api/product/checks/settings", "body": whole})["body"] == whole


def _nia_ctx(admin_id: int | None = None) -> tools.Context:
    nia = registry.AGENTS["nia"]
    return tools.Context(agent=nia, run_id=0, db=_db, granted=tools.usable(nia, []), admin_id=admin_id)


def test_nia_always_sees_what_really_became_of_her_proposals() -> None:
    now = datetime.now(UTC)
    with _db() as db:
        db.add(TeamApproval(agent="nia", action_type="admin.change", level="L3", title="Set Customer Success Rules", payload={}, status="failed",
                            evidence=[], result={"status": 422, "answer": {"error": {"message": "Please check these fields.",
                                                                                      "fields": [{"field": "feedback.moments.1", "message": "Input should be 'feedback'"}]}}},
                            created_at=now, decided_at=now, executed_at=now))
        db.add(OwnerDirective(text="Check the festival pages", agents=["qa_runner"], status="proposed", reports=[], created_at=now))
        db.flush()
        context = memory.context(db)
    assert truth.RULES in context and "Set Customer Success Rules" in context and "'failed'" in context and "feedback.moments.1" in context
    assert "'proposed'" in context, "the directive still waiting for the owner"
    ctx = _nia_ctx()
    assert "proposal_status" in ctx.granted, "she always has it, nothing to tick"
    seen = tools.run(ctx, "proposal_status", {})
    assert seen["proposals"][0]["status"] == "failed" and "feedback.moments.1" in seen["proposals"][0]["why_failed"]
    assert seen["directives"][0]["status"] == "proposed"


def test_a_directive_reaches_the_agents_only_when_the_owner_said_yes() -> None:
    with _db() as db:
        row = OwnerDirective(text="Check the pages", agents=["qa_runner"], status="proposed", reports=[], created_at=datetime.now(UTC))
        db.add(row)
        db.flush()
        directive_id = row.id
    nia = registry.AGENTS["nia"]
    without_owner = tools.Context(agent=nia, run_id=0, db=_db, granted=("confirm_directive",))
    assert "error" in tools.run(without_owner, "confirm_directive", {"directive_id": directive_id}), "not in a scheduled run"
    with_owner = tools.Context(agent=nia, run_id=0, db=_db, granted=("confirm_directive",), admin_id=1)
    assert tools.run(with_owner, "confirm_directive", {"directive_id": directive_id})["status"] == "active"


def test_a_voice_turn_that_only_used_tools_is_kept_with_the_answer() -> None:
    done, following = Turn(owner=["Take me to approvals"], tools=[{"tool": "show_page", "ok": True}]), Turn(nia=["I opened Approvals."])
    assert carry(done, following)
    assert following.owner == ["Take me to approvals"] and following.tools == [{"tool": "show_page", "ok": True}]
    assert not carry(Turn(nia=["Hello"], tools=[{"tool": "x"}]), Turn()), "a turn with an answer is saved as it is"


def test_the_owner_hears_of_a_proposal_that_failed(admin: Any) -> None:  # noqa: F811 - the fixture
    now = datetime.now(UTC)
    with _db() as db:
        db.add(TeamApproval(agent="nia", action_type="admin.change", level="L3", title="Set Customer Success Rules", payload={}, status="failed",
                            evidence=[], result={"status": 422}, created_at=now, decided_at=now, executed_at=now))
    items = admin.get("/admin-api/notifications").json()["items"]
    failed = [i for i in items if i["kind"] == "proposal_failed"]
    assert failed and failed[0]["params"]["title"] == "Set Customer Success Rules" and failed[0]["link"] == "/approvals"
