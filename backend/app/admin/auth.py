"""Admin accounts and sign-in: password, then an authenticator code, every time.

Admins are a separate table (`admin_users`) from members. There is no sign-up:
the first admin is created with `python -m app.admin_cli create-owner`, which
prints a one-time link; later admins are invited by an owner. Accepting an
invitation sets the password; the first sign-in enrols the authenticator app.

A session whose password was accepted but whose code was not (`mfa_passed`
false) can do nothing except finish signing in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import settings
from ..core import crypto, totp
from ..core.errors import Conflict, Forbidden, RateLimited, Unauthorized, ValidationFailed
from ..core.passwords import (
    hash_password,
    new_token,
    password_policy_errors,
    token_digest,
    verify_password,
)
from ..models import AdminAuditLog, AdminSession, AdminUser, LoginAttempt

ROLES = ("owner", "support")
INVITE_HOURS = 72


def record(session: DbSession, admin: AdminUser | None, action: str, *, target_type: str = "", target_id: str | int = "", ip: str = "", result: str = "ok", metadata: dict[str, Any] | None = None) -> None:
    """Append to the admin log. Nothing in the admin code updates or deletes it. A target longer than its column (many ids
    decided at once) is kept whole in the metadata, so the log never fails the owner's action."""
    target = str(target_id)
    if len(target) > 100:
        metadata = {**(metadata or {}), "targets": target}
        target = target[:97] + "…"
    session.add(
        AdminAuditLog(
            timestamp=datetime.now(UTC),
            admin_user_id=admin.id if admin else None,
            admin_email=admin.email if admin else "",
            action=action,
            target_type=target_type,
            target_id=target,
            ip_address=ip[:64],
            result=result,
            metadata_=metadata or {},
        )
    )


def _persist_failure(keys: list[str], *, admin_id: int | None = None, lock_for: timedelta | None = None) -> None:
    """Failed attempts are written in their own transaction: the request's is rolled
    back by the error that follows, and lockout must still count them."""
    from ..db import admin_transaction

    now = datetime.now(UTC)
    with admin_transaction(write=True) as own:
        for key in keys:
            own.add(LoginAttempt(key=key, attempted_at=now, success=False))
        if admin_id is not None:
            row = own.get(AdminUser, admin_id)
            if row is not None:
                row.failed_login_count += 1
                if lock_for and row.failed_login_count >= 5:
                    row.locked_until = now + lock_for


def _persist_record(admin_id: int | None, email: str, action: str, ip: str, metadata: dict[str, Any]) -> None:
    from ..db import admin_transaction

    with admin_transaction(write=True) as own:
        own.add(AdminAuditLog(timestamp=datetime.now(UTC), admin_user_id=admin_id, admin_email=email, action=action, ip_address=ip[:64], result="denied", metadata_=metadata))


# --- invitations ------------------------------------------------------------------------
def invite(session: DbSession, *, email: str, full_name: str, role: str, invited_by: AdminUser | None) -> tuple[AdminUser, str]:
    if role not in ROLES:
        raise ValidationFailed("Unknown role.", code="INVALID_ROLE")
    normalized = email.strip().lower()
    existing = session.execute(select(AdminUser).where(AdminUser.email == normalized)).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        raise Conflict("An admin with this e-mail already exists.", code="ADMIN_EXISTS")
    now = datetime.now(UTC)
    raw = new_token()
    admin = existing or AdminUser(email=normalized, created_at=now, updated_at=now)
    admin.full_name = full_name.strip()
    admin.role = role
    admin.status = "invited"
    admin.password_hash = ""
    admin.totp_secret_ciphertext = ""
    admin.totp_enabled_at = None
    admin.invite_token_hash = token_digest(raw)
    admin.invite_expires_at = now + timedelta(hours=INVITE_HOURS)
    admin.invited_by_admin_id = invited_by.id if invited_by else None
    admin.updated_at = now
    if existing is None:
        session.add(admin)
    session.flush()
    return admin, raw


def invite_link(raw: str) -> str:
    base = (settings().admin_public_base_url or "http://localhost:5174").rstrip("/")
    return f"{base}/accept-invite?token={raw}"


def accept_invite(session: DbSession, *, token: str, password: str) -> AdminUser:
    problems = password_policy_errors(password)
    if problems:
        raise ValidationFailed(" ".join(problems), code="WEAK_PASSWORD")
    admin = session.execute(select(AdminUser).where(AdminUser.invite_token_hash == token_digest(token))).scalar_one_or_none()
    now = datetime.now(UTC)
    if admin is None or admin.status != "invited" or not admin.invite_expires_at or admin.invite_expires_at <= now:
        raise ValidationFailed("This invitation is invalid or has expired.", code="TOKEN_INVALID")
    admin.password_hash = hash_password(password)
    admin.status = "active"
    admin.invite_token_hash = None
    admin.invite_expires_at = None
    admin.updated_at = now
    record(session, admin, "admin.invite_accepted", target_type="admin", target_id=admin.id)
    return admin


def change_password(session: DbSession, admin: AdminUser, current: AdminSession, *, old: str, new: str, ip: str) -> None:
    """An admin changes their own password: the current one first, the policy for the new one. Every other session of
    theirs ends (a stolen session does not survive the change); the one in use stays signed in."""
    if not verify_password(old, admin.password_hash):
        _persist_record(admin.id, admin.email, "admin.password_change_refused", ip, {"reason": "wrong current password"})
        raise ValidationFailed("The current password is not right.", code="WRONG_PASSWORD")
    if old == new:
        raise ValidationFailed("Choose a new password, not the current one.", code="SAME_PASSWORD")
    problems = password_policy_errors(new)
    if problems:
        raise ValidationFailed(" ".join(problems), code="WEAK_PASSWORD")
    now = datetime.now(UTC)
    admin.password_hash = hash_password(new)
    admin.updated_at = now
    others = session.execute(select(AdminSession).where(AdminSession.admin_user_id == admin.id, AdminSession.id != current.id,
                                                        AdminSession.revoked_at.is_(None))).scalars().all()
    for row in others:
        row.revoked_at = now
    record(session, admin, "admin.password_changed", target_type="admin", target_id=admin.id, ip=ip, metadata={"other_sessions_ended": len(others)})


# --- sign-in ----------------------------------------------------------------------------------
def _failures(session: DbSession, key: str, window: timedelta) -> int:
    since = datetime.now(UTC) - window
    return len(session.execute(select(LoginAttempt.id).where(LoginAttempt.key == key, LoginAttempt.attempted_at >= since, LoginAttempt.success.is_(False))).all())


def login(session: DbSession, *, email: str, password: str, ip: str, user_agent: str) -> tuple[AdminUser, AdminSession, str]:
    cfg = settings()
    normalized = email.strip().lower()
    window = timedelta(minutes=cfg.login_lockout_minutes)
    now = datetime.now(UTC)
    if _failures(session, f"admin-ip:{ip}", window) >= 10 or _failures(session, f"admin-acct:{normalized}", window) >= 5:
        raise RateLimited("Too many attempts. Try again later.", code="LOGIN_THROTTLED")
    admin = session.execute(select(AdminUser).where(AdminUser.email == normalized)).scalar_one_or_none()
    ok = bool(admin and admin.status == "active" and admin.password_hash and verify_password(password, admin.password_hash))
    if admin and admin.locked_until and admin.locked_until > now:
        ok = False
    if not ok or admin is None:
        # Written after the rollback this error causes (core/errors.AppError).
        error = Unauthorized("Invalid e-mail or password.", code="INVALID_CREDENTIALS")
        admin_id, admin_email = (admin.id, admin.email) if admin else (None, "")
        error.after_rollback.append(lambda: _persist_failure([f"admin-ip:{ip}", f"admin-acct:{normalized}"], admin_id=admin_id, lock_for=window))
        error.after_rollback.append(lambda: _persist_record(admin_id, admin_email, "admin.login_failed", ip, {"email": normalized}))
        raise error
    for key in (f"admin-ip:{ip}", f"admin-acct:{normalized}"):
        session.add(LoginAttempt(key=key, attempted_at=now, success=True))
    admin.failed_login_count = 0
    admin.locked_until = None
    raw = new_token()
    row = AdminSession(
        admin_user_id=admin.id,
        token_hash=token_digest(raw),
        csrf_token=new_token(24),
        mfa_passed=False,
        created_at=now,
        expires_at=now + timedelta(hours=cfg.admin_session_hours),
        last_seen_at=now,
        ip_address=ip[:64],
        user_agent=user_agent[:300],
    )
    session.add(row)
    session.flush()
    record(session, admin, "admin.password_accepted", ip=ip)
    return admin, row, raw


def resolve(session: DbSession, raw: str) -> tuple[AdminUser, AdminSession] | None:
    cfg = settings()
    now = datetime.now(UTC)
    row = session.execute(select(AdminSession).where(AdminSession.token_hash == token_digest(raw))).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    if row.last_seen_at + timedelta(minutes=cfg.admin_idle_minutes) <= now:
        row.revoked_at = now
        return None
    admin = session.get(AdminUser, row.admin_user_id)
    if admin is None or admin.status != "active":
        return None
    row.last_seen_at = now
    return admin, row


def logout(session: DbSession, row: AdminSession, admin: AdminUser, ip: str) -> None:
    row.revoked_at = datetime.now(UTC)
    record(session, admin, "admin.logout", ip=ip)


# --- the authenticator app --------------------------------------------------------------------
def _secret(admin: AdminUser) -> str:
    if not admin.totp_secret_ciphertext:
        return ""
    return crypto.decrypt(admin.totp_secret_ciphertext, associated_data=f"admin-totp:{admin.id}")


def start_enrolment(session: DbSession, admin: AdminUser) -> dict[str, str]:
    if admin.totp_enabled_at is not None:
        raise Conflict("An authenticator is already set up. Ask an owner to reset it.", code="MFA_ALREADY_ENROLLED")
    secret = totp.new_secret()
    admin.totp_secret_ciphertext = crypto.encrypt(secret, associated_data=f"admin-totp:{admin.id}")
    admin.updated_at = datetime.now(UTC)
    uri = totp.provisioning_uri(secret, account=admin.email, issuer=settings().admin_totp_issuer)
    import segno

    svg = segno.make(uri, error="m").svg_inline(scale=5, dark="#111827", light="#ffffff")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": svg}


def check_code(session: DbSession, admin: AdminUser, row: AdminSession, code: str, *, enrolling: bool, ip: str) -> None:
    if enrolling and admin.totp_enabled_at is not None:
        raise Conflict("An authenticator is already set up.", code="MFA_ALREADY_ENROLLED")
    if not enrolling and admin.totp_enabled_at is None:
        raise Forbidden("Set up your authenticator app first.", code="MFA_ENROLMENT_REQUIRED")
    secret = _secret(admin)
    if not secret:
        raise Forbidden("Start the authenticator set-up first.", code="MFA_ENROLMENT_REQUIRED")
    key = f"admin-mfa:{admin.id}"
    if _failures(session, key, timedelta(minutes=15)) >= 5:
        raise RateLimited("Too many wrong codes. Sign in again later.", code="MFA_THROTTLED")
    step = totp.verify(secret, code, last_step=admin.totp_last_step)
    now = datetime.now(UTC)
    if step is None:
        error = Unauthorized("That code is not valid. Use the current code from your authenticator app.", code="MFA_INVALID")
        admin_id, admin_email = admin.id, admin.email
        error.after_rollback.append(lambda: _persist_failure([key]))
        error.after_rollback.append(lambda: _persist_record(admin_id, admin_email, "admin.mfa_failed", ip, {}))
        raise error
    session.add(LoginAttempt(key=key, attempted_at=now, success=True))
    admin.totp_last_step = step
    if enrolling:
        admin.totp_enabled_at = now
    admin.last_login_at = now
    admin.updated_at = now
    row.mfa_passed = True
    record(session, admin, "admin.mfa_enrolled" if enrolling else "admin.login", ip=ip)


def reset_mfa(session: DbSession, target: AdminUser) -> None:
    target.totp_secret_ciphertext = ""
    target.totp_enabled_at = None
    target.totp_last_step = 0
    target.updated_at = datetime.now(UTC)
    for row in session.execute(select(AdminSession).where(AdminSession.admin_user_id == target.id, AdminSession.revoked_at.is_(None))).scalars():
        row.revoked_at = datetime.now(UTC)
