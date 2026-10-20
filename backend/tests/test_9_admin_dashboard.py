"""The admin (app/main.py): its own sign-in with an authenticator code, its own cookie, what an
admin can and cannot do, and which addresses stay open to outside services."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin_cli import main as admin_cli
from app.core import totp
from app.db import admin_transaction, transaction
from app.main import app as admin_app
from app.models import AdminAuditLog, AdminUser, AgentTask

OWNER = {"email": "owner@tower.example", "password": "owner-passphrase-long-2026", "name": "Olga Owner"}


class AdminClient(TestClient):
    csrf: str = ""

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        if self.csrf and method.upper() not in ("GET", "HEAD", "OPTIONS"):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", self.csrf)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def _secret_of(email: str) -> str:
    from app.core import crypto

    with admin_transaction() as db:
        admin = db.scalar(select(AdminUser).where(AdminUser.email == email))
        assert admin is not None
        return crypto.decrypt(admin.totp_secret_ciphertext, associated_data=f"admin-totp:{admin.id}")


def _fresh_code(secret: str, email: str) -> str:
    """The code for the next unused time step (a code is accepted only once)."""
    with admin_transaction() as db:
        last = db.scalar(select(AdminUser.totp_last_step).where(AdminUser.email == email)) or 0
    step = max(totp.current_step(), last + 1)
    return totp.code(secret, now=step * totp.STEP_SECONDS + 1)


@pytest.fixture(scope="module")
def owner() -> Iterator[AdminClient]:
    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with redirect_stdout(buffer):
        admin_cli(["create-owner", "--email", OWNER["email"], "--name", OWNER["name"]])
    link = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if "accept-invite?token=" in line)
    token = parse_qs(urlparse(link).query)["token"][0]
    with AdminClient(admin_app) as c:
        assert c.post("/admin-api/auth/accept-invite", json={"token": token, "password": OWNER["password"]}).status_code == 200
        assert c.post("/admin-api/auth/accept-invite", json={"token": token, "password": OWNER["password"]}).status_code == 422, "an invitation works once"
        r = c.post("/admin-api/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
        assert r.status_code == 200 and r.json()["next"] == "enroll", r.text
        c.csrf = r.json()["csrf_token"]
        # Until the code is entered, nothing but the sign-in steps works.
        assert c.get("/admin-api/overview").json()["error"]["code"] == "MFA_REQUIRED"
        enrolment = c.post("/admin-api/auth/enroll/start").json()
        assert enrolment["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in enrolment["qr_svg"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": "000000"}).status_code == 401
        r = c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(enrolment["secret"], OWNER["email"])})
        assert r.status_code == 200 and r.json()["mfa_passed"] is True, r.text
        yield c


def _log(action: str) -> list[Any]:
    with admin_transaction() as db:
        return db.scalars(select(AdminAuditLog).where(AdminAuditLog.action == action)).all()


def test_second_sign_in_needs_the_code_and_a_code_works_once(owner: AdminClient) -> None:
    secret = _secret_of(OWNER["email"])
    with AdminClient(admin_app) as c:
        r = c.post("/admin-api/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
        assert r.json()["next"] == "verify"
        c.csrf = r.json()["csrf_token"]
        with admin_transaction() as db:
            used = db.scalar(select(AdminUser.totp_last_step).where(AdminUser.email == OWNER["email"]))
        replay = totp.code(secret, now=used * totp.STEP_SECONDS + 1)
        assert c.post("/admin-api/auth/verify", json={"code": replay}).status_code == 401, "an accepted code cannot be replayed"
        assert c.post("/admin-api/auth/verify", json={"code": _fresh_code(secret, OWNER["email"])}).status_code == 200
        assert c.get("/admin-api/overview").status_code == 200
        # the cookie is needed for CSRF-protected changes as well
        assert c.post("/admin-api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_without_a_session_nothing_answers(anon: TestClient) -> None:
    assert anon.get("/admin-api/overview").status_code == 401
    anon.cookies.set("tower_admin", "made-up")
    assert anon.get("/admin-api/overview").status_code == 401


def test_the_overview_is_about_the_agents_not_members(owner: AdminClient) -> None:
    overview = owner.get("/admin-api/overview").json()
    assert {"agents_total", "agents_on", "runs_today", "proposals_waiting", "ai_spend_today_usd"} <= set(overview)
    assert not {k for k in overview if "member" in k or "signup" in k or "opportunit" in k}
    # No member or Epliv page is left.
    for gone in ("/admin-api/members", "/admin-api/invitations", "/admin-api/listings", "/admin-api/plans"):
        assert owner.get(gone).status_code == 404, gone


def test_platform_switches_pause_everything(owner: AdminClient) -> None:
    from app.core.errors import AutomationPaused
    from app.workers.worker import check_switches

    assert owner.put("/admin-api/platform", json={"switches": {"paused_all": True}}).status_code == 200
    with pytest.raises(AutomationPaused):
        check_switches("agents")
    assert owner.put("/admin-api/platform", json={"switches": {"stop_new_applications": True}}).status_code == 422, "unknown switches are refused"
    assert owner.put("/admin-api/platform", json={"switches": {"paused_all": False}}).status_code == 200
    check_switches("agents")


def test_support_admins_cannot_change_the_platform(owner: AdminClient) -> None:
    invited = owner.post("/admin-api/admins", json={"email": "support@tower.example", "full_name": "Sam Support", "role": "support"}).json()
    token = parse_qs(urlparse(invited["link"]).query)["token"][0]
    with AdminClient(admin_app) as c:
        assert c.post("/admin-api/auth/accept-invite", json={"token": token, "password": "support-passphrase-2026"}).status_code == 200
        r = c.post("/admin-api/auth/login", json={"email": "support@tower.example", "password": "support-passphrase-2026"})
        c.csrf = r.json()["csrf_token"]
        secret = c.post("/admin-api/auth/enroll/start").json()["secret"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(secret, "support@tower.example")}).status_code == 200
        assert c.get("/admin-api/overview").status_code == 200
        assert c.put("/admin-api/platform", json={"maintenance_banner": "x"}).status_code == 403
        assert c.get("/admin-api/admins").status_code == 403


def test_a_cancelled_task_is_not_retried(owner: AdminClient) -> None:
    with transaction(write=True) as db:
        task = AgentTask(queue="default", task_type="team.run", payload={}, status="cancelled", run_at=datetime.now(UTC), correlation_id="cancelled-by-owner")
        db.add(task)
        db.flush()
        task_id = task.id
    assert owner.post(f"/admin-api/tasks/{task_id}/retry").status_code == 422
    page = owner.get("/admin-api/tasks").json()
    assert "member_id" not in page["items"][0] and "agents" in page["depths"] or page["depths"]


def test_admin_log_has_every_change(owner: AdminClient) -> None:
    rows = owner.get("/admin-api/audit", params={"page_size": 200}).json()["items"]
    actions = {r["action"] for r in rows}
    assert {"admin.mfa_enrolled", "platform.changed", "admin.invited"} <= actions


def test_the_ip_allowlist_keeps_the_admin_closed_but_not_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main
    from app.config import settings

    monkeypatch.setattr(settings(), "admin_ip_allowlist", "203.0.113.7")
    app = main.create_app()
    with TestClient(app) as c:
        assert c.get("/admin-api/health").status_code == 404, "the admin answers only the owner's addresses"
        assert c.get("/agent-gateway/tools").status_code == 401, "the gateway answers anyone — and asks for a credential"
        assert c.get("/api/v1/media/" + "a" * 24 + ".jpg").status_code == 404, "media links answer anyone (an unknown one is simply not found)"
        assert c.get("/.well-known/oauth-authorization-server").status_code == 200
