"""Admin request dependencies: the admin cookie, CSRF, the authenticator step, roles."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from ..config import settings
from ..core.errors import Forbidden, Unauthorized
from ..core.net import client_ip
from ..core.passwords import constant_time_equals
from ..db import admin_transaction
from ..models import AdminSession, AdminUser
from . import auth

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def admin_db() -> Iterator[DbSession]:
    with admin_transaction(write=True) as session:
        yield session


# scope="function": commit before the response is sent. With the default
# ("request") the commit comes afterwards, and the page that reads again at once
# (after the authenticator code) could see the old state and sign the admin out.
AdminDb = Annotated[DbSession, Depends(admin_db, scope="function")]


@dataclass
class AdminContext:
    admin: AdminUser
    session: AdminSession
    ip: str


def _signed_in(request: Request, session: AdminDb) -> AdminContext:
    raw = request.cookies.get(settings().admin_session_cookie_name, "")
    if not raw:
        raise Unauthorized("Sign in to continue.", code="NOT_AUTHENTICATED")
    resolved = auth.resolve(session, raw)
    if resolved is None:
        raise Unauthorized("Session expired. Sign in again.", code="SESSION_INVALID")
    admin, row = resolved
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token", "")
        if not header or not constant_time_equals(header, row.csrf_token):
            raise Forbidden("Missing or invalid CSRF token.", code="CSRF_FAILED")
    return AdminContext(admin=admin, session=row, ip=client_ip(request))


def pending_admin(ctx: Annotated[AdminContext, Depends(_signed_in)]) -> AdminContext:
    """Password accepted; the authenticator step may still be open."""
    return ctx


def current_admin(ctx: Annotated[AdminContext, Depends(_signed_in)]) -> AdminContext:
    if not ctx.session.mfa_passed:
        raise Unauthorized("Enter the code from your authenticator app.", code="MFA_REQUIRED")
    return ctx


def owner_admin(ctx: Annotated[AdminContext, Depends(current_admin)]) -> AdminContext:
    if ctx.admin.role != "owner":
        raise Forbidden("Only an owner can do this.", code="OWNER_ONLY")
    return ctx


def owner_admin_brief(request: Request) -> AdminContext:
    """The owner, checked in a transaction of its own that is closed before the request's work starts. For requests that
    wait on a model for longer than the database lets a transaction sit idle (a minute): with OwnerAdmin the sign-in's
    transaction stayed open while the agent thought, the database ended it, and the answer came back as an error."""
    with admin_transaction(write=True) as session:
        ctx = owner_admin(current_admin(_signed_in(request, session)))
        session.flush()
        # Kept usable after the transaction: the route reads the admin's id and name, never loads more.
        session.expunge(ctx.admin)
        session.expunge(ctx.session)
    return ctx


PendingAdmin = Annotated[AdminContext, Depends(pending_admin)]
CurrentAdmin = Annotated[AdminContext, Depends(current_admin)]
OwnerAdmin = Annotated[AdminContext, Depends(owner_admin)]
OwnerAdminBrief = Annotated[AdminContext, Depends(owner_admin_brief)]
