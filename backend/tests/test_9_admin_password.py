"""An admin changes their own password (Admin → My account): the current password first, the policy for the new
one; every other sign-in of theirs ends, this one stays; the change is in the admin log."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from sqlalchemy import select

from app.admin_cli import main as admin_cli
from app.db import admin_transaction
from app.main import app as admin_app
from app.models import AdminAuditLog
from tests.test_9_admin_dashboard import AdminClient, _fresh_code

OLD = "first-owner-passphrase-2026"
NEW = "second-owner-passphrase-2026"


def _signed_in(email: str, password: str, secret: str = "") -> tuple[AdminClient, str]:
    c = AdminClient(admin_app).__enter__()  # started like a `with` block; closed with the test process
    r = c.post("/admin-api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.csrf = r.json()["csrf_token"]
    if not secret:
        secret = c.post("/admin-api/auth/enroll/start").json()["secret"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(secret, email)}).status_code == 200
    else:
        assert c.post("/admin-api/auth/verify", json={"code": _fresh_code(secret, email)}).status_code == 200
    return c, secret


def test_an_admin_changes_their_password_and_other_sign_ins_end() -> None:
    email = f"pw-owner-{uuid4().hex[:6]}@tower.example"
    buffer = StringIO()
    with redirect_stdout(buffer):
        admin_cli(["create-owner", "--email", email, "--name", "Pia Password"])
    link = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if "accept-invite?token=" in line)
    with AdminClient(admin_app) as setup:
        assert setup.post("/admin-api/auth/accept-invite", json={"token": parse_qs(urlparse(link).query)["token"][0], "password": OLD}).status_code == 200
    here, secret = _signed_in(email, OLD)
    elsewhere, _ = _signed_in(email, OLD, secret)

    wrong = here.post("/admin-api/auth/password", json={"current_password": "not-the-password-at-all", "new_password": NEW})
    assert wrong.status_code in (400, 422) and "current password" in wrong.text
    weak = here.post("/admin-api/auth/password", json={"current_password": OLD, "new_password": "short"})
    assert weak.status_code == 422
    same = here.post("/admin-api/auth/password", json={"current_password": OLD, "new_password": OLD})
    assert same.status_code in (400, 422) and "new password" in same.text

    done = here.post("/admin-api/auth/password", json={"current_password": OLD, "new_password": NEW})
    assert done.status_code == 200 and "other sign-ins were ended" in done.json()["message"]
    assert here.get("/admin-api/auth/me").status_code == 200, "this sign-in stays"
    assert elsewhere.get("/admin-api/auth/me").status_code == 401, "every other sign-in ends"
    with AdminClient(admin_app) as fresh:
        assert fresh.post("/admin-api/auth/login", json={"email": email, "password": OLD}).status_code == 401
        assert fresh.post("/admin-api/auth/login", json={"email": email, "password": NEW}).json()["next"] == "verify"
    with admin_transaction() as db:
        actions = set(db.scalars(select(AdminAuditLog.action).where(AdminAuditLog.admin_email == email)))
    assert {"admin.password_changed", "admin.password_change_refused"} <= actions


def test_a_half_signed_in_session_cannot_change_the_password() -> None:
    email = f"pw-half-{uuid4().hex[:6]}@tower.example"
    buffer = StringIO()
    with redirect_stdout(buffer):
        admin_cli(["create-owner", "--email", email, "--name", "Hal Half"])
    link = next(line.split(": ", 1)[1] for line in buffer.getvalue().splitlines() if "accept-invite?token=" in line)
    c = AdminClient(admin_app).__enter__()
    c.post("/admin-api/auth/accept-invite", json={"token": parse_qs(urlparse(link).query)["token"][0], "password": OLD})
    r = c.post("/admin-api/auth/login", json={"email": email, "password": OLD})
    c.csrf = r.json()["csrf_token"]
    assert c.post("/admin-api/auth/password", json={"current_password": OLD, "new_password": NEW}).status_code == 401, "the authenticator code comes first"
