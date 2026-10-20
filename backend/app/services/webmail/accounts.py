"""Mail accounts and who may use them.

An account is a mailbox the team adds like in Outlook: its address, the login, the password (stored
encrypted, core/crypto.py, never shown again) and the IMAP and SMTP servers. A team mailbox from
Admin → Team → Mailboxes (the marketing mailbox) is linked rather than copied, so its settings stay in
one place. Who may open an account, send from it and change it is in `mail_access`: the one who adds an
account manages it; an owner may manage every account's settings and access (each change is in the
admin log), but reads its mail only once they gave themselves access.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...core import crypto
from ...core.errors import Forbidden, NotFound, ValidationFailed
from ...models import AdminUser, MailAccess, MailAccount
from ..team import mailboxes

Security = Literal["ssl", "starttls"]


# Server names of the providers people use most, so adding an account needs only address and password.
# A note says what the provider asks for first (an app password, IMAP switched on).
PRESETS: list[dict[str, Any]] = [
    {"key": "ionos", "label": "IONOS", "domains": ["ionos.de", "ionos.com"], "imap_host": "imap.ionos.de", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "smtp.ionos.de", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": True, "note": ""},
    {"key": "gmail", "label": "Gmail / Google Workspace", "domains": ["gmail.com", "googlemail.com"], "imap_host": "imap.gmail.com", "imap_port": 993,
     "imap_security": "ssl", "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": False,
     "note": "Google needs an app password (Google account → Security → 2-Step Verification → App passwords); your normal password is refused."},
    {"key": "microsoft", "label": "Outlook.com / Microsoft 365", "domains": ["outlook.com", "hotmail.com", "live.com", "outlook.de", "hotmail.de"],
     "imap_host": "outlook.office365.com", "imap_port": 993, "imap_security": "ssl", "smtp_host": "smtp.office365.com", "smtp_port": 587,
     "smtp_security": "starttls", "save_sent_copy": False,
     "note": "Microsoft accepts a password only where the administrator still allows it (IMAP and SMTP AUTH on, app password with two-step sign-in)."},
    {"key": "strato", "label": "Strato", "domains": ["strato.de"], "imap_host": "imap.strato.de", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "smtp.strato.de", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": True, "note": ""},
    {"key": "gmx", "label": "GMX", "domains": ["gmx.de", "gmx.net", "gmx.at", "gmx.ch"], "imap_host": "imap.gmx.net", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "mail.gmx.net", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": True,
     "note": "Switch on \"POP3/IMAP access\" in the GMX settings first."},
    {"key": "webde", "label": "WEB.DE", "domains": ["web.de"], "imap_host": "imap.web.de", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "smtp.web.de", "smtp_port": 587, "smtp_security": "starttls", "save_sent_copy": True,
     "note": "Switch on \"POP3/IMAP access\" in the WEB.DE settings first."},
    {"key": "telekom", "label": "T-Online", "domains": ["t-online.de"], "imap_host": "secureimap.t-online.de", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "securesmtp.t-online.de", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": True,
     "note": "T-Online needs its separate e-mail password (not the Telekom login)."},
    {"key": "icloud", "label": "iCloud", "domains": ["icloud.com", "me.com", "mac.com"], "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls", "save_sent_copy": True,
     "note": "Apple needs an app-specific password (appleid.apple.com → Sign-In and Security)."},
    {"key": "yahoo", "label": "Yahoo", "domains": ["yahoo.com", "yahoo.de"], "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_security": "ssl",
     "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_security": "ssl", "save_sent_copy": False,
     "note": "Yahoo needs an app password (Account security → Generate app password)."},
]


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    security: str
    login: str
    password: str


class AccountIn(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    display_name: str = Field(default="", max_length=200)
    login: str = Field(default="", max_length=254)
    # None keeps the stored password; "" is refused for a new account.
    password: str | None = Field(default=None, max_length=500)
    imap_host: str = Field(default="", max_length=200)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Security = "ssl"
    smtp_host: str = Field(default="", max_length=200)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Security = "ssl"
    save_sent_copy: bool = True
    language: Literal["de", "en"] = "de"
    color: str = Field(default="", max_length=20)
    sync_days: int = Field(default=90, ge=7, le=3650)

    @field_validator("address", "login")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("imap_host", "smtp_host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not all(c.isalnum() or c in ".-" for c in value):
            raise ValueError("A server name has only letters, digits, dots and hyphens.")
        return value


def _aad(account_id: int) -> str:
    return f"mail_account:{account_id}"


def check_host(host: str) -> None:
    """A mail server must be on the internet: not this machine, not the private network the service runs in."""
    if settings().webmail_allow_private_hosts:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValidationFailed(f"The server {host} cannot be found.", code="MAIL_HOST_UNKNOWN") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified:
            raise ValidationFailed(f"The server {host} is not on the internet.", code="MAIL_HOST_PRIVATE")


def imap_server(session: Session, account: MailAccount) -> Server:
    if account.team_mailbox:
        box = mailboxes.view(session, account.team_mailbox)
        if box.missing:
            raise ValidationFailed("The team mailbox is not fully set up (Team → Mailboxes).", code="MAIL_NOT_SET_UP")
        server = Server(box.imap_host, int(box.imap_port or 993), "ssl", box.login, mailboxes.secret(session, account.team_mailbox))
    else:
        password = crypto.decrypt(account.password_enc, associated_data=_aad(account.id)) if account.password_enc else ""
        server = Server(account.imap_host, account.imap_port, account.imap_security, account.login or account.address, password)
    check_host(server.host)
    return server


def smtp_server(session: Session, account: MailAccount) -> Server:
    if account.team_mailbox:
        box = mailboxes.view(session, account.team_mailbox)
        if box.missing:
            raise ValidationFailed("The team mailbox is not fully set up (Team → Mailboxes).", code="MAIL_NOT_SET_UP")
        server = Server(box.smtp_host, int(box.smtp_port or 465), box.smtp_security or "ssl", box.login, mailboxes.secret(session, account.team_mailbox))
    else:
        password = crypto.decrypt(account.password_enc, associated_data=_aad(account.id)) if account.password_enc else ""
        server = Server(account.smtp_host, account.smtp_port, account.smtp_security, account.login or account.address, password)
    check_host(server.host)
    return server


def sender(session: Session, account: MailAccount) -> tuple[str, str]:
    """(name, address) the account sends as."""
    if account.team_mailbox:
        box = mailboxes.view(session, account.team_mailbox)
        return account.display_name or box.sender_name, box.address or account.address
    return account.display_name, account.address


def preset_for(address: str) -> dict[str, Any] | None:
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    return next((p for p in PRESETS if domain in p["domains"]), None)


# --- access ----------------------------------------------------------------------------------------------
def access_of(session: Session, account_id: int, admin_id: int) -> MailAccess | None:
    return session.execute(select(MailAccess).where(MailAccess.account_id == account_id, MailAccess.admin_id == admin_id)).scalar_one_or_none()


def require(session: Session, admin: AdminUser, account_id: int, *, send: bool = False, manage: bool = False) -> tuple[MailAccount, MailAccess | None]:
    """The account, if this admin may open it (send from it, manage it); NotFound otherwise, so nobody
    learns of accounts they have no access to."""
    account = session.get(MailAccount, account_id)
    if account is None:
        raise NotFound("No such mail account.", code="MAIL_ACCOUNT_NOT_FOUND")
    access = access_of(session, account_id, admin.id)
    if manage:
        if admin.role == "owner" or (access and access.can_manage):
            return account, access
        if access is None:
            raise NotFound("No such mail account.", code="MAIL_ACCOUNT_NOT_FOUND")
        raise Forbidden("Only who manages this account can change it.", code="MAIL_MANAGE_ONLY")
    if access is None:
        raise NotFound("No such mail account.", code="MAIL_ACCOUNT_NOT_FOUND")
    if send and not access.can_send:
        raise Forbidden("You may read this account but not send from it.", code="MAIL_READ_ONLY")
    return account, access


def member(session: Session, admin: AdminUser, account_id: int, *, send: bool = False) -> tuple[MailAccount, MailAccess]:
    """The account and this admin's access to it (to read, or to send from it)."""
    account, access = require(session, admin, account_id, send=send)
    if access is None:
        raise NotFound("No such mail account.", code="MAIL_ACCOUNT_NOT_FOUND")
    return account, access


def visible(session: Session, admin: AdminUser) -> list[tuple[MailAccount, MailAccess]]:
    rows = session.execute(select(MailAccount, MailAccess).join(MailAccess, MailAccess.account_id == MailAccount.id)
                           .where(MailAccess.admin_id == admin.id).order_by(MailAccount.id)).all()
    return [(a, x) for a, x in rows]


def visible_ids(session: Session, admin: AdminUser) -> list[int]:
    return list(session.execute(select(MailAccess.account_id).where(MailAccess.admin_id == admin.id)).scalars())


def create(session: Session, admin: AdminUser, data: AccountIn) -> MailAccount:
    if not data.password:
        raise ValidationFailed("Enter the account's password.", code="MAIL_PASSWORD_MISSING")
    if not data.imap_host or not data.smtp_host:
        raise ValidationFailed("Enter the incoming (IMAP) and outgoing (SMTP) servers.", code="MAIL_SERVERS_MISSING")
    check_host(data.imap_host)
    check_host(data.smtp_host)
    now = datetime.now(UTC)
    account = MailAccount(**data.model_dump(exclude={"password"}), created_by_admin_id=admin.id, created_at=now, updated_at=now)
    session.add(account)
    session.flush()
    account.password_enc = crypto.encrypt(data.password, associated_data=_aad(account.id))
    session.add(MailAccess(account_id=account.id, admin_id=admin.id, can_send=True, can_manage=True, created_at=now))
    session.flush()
    return account


def link_team_mailbox(session: Session, admin: AdminUser, name: str) -> MailAccount:
    box = mailboxes.view(session, name)
    if box.missing:
        raise ValidationFailed("Set the mailbox up in Team → Mailboxes first: " + ", ".join(box.missing), code="MAIL_NOT_SET_UP")
    if session.execute(select(MailAccount.id).where(MailAccount.team_mailbox == name)).first():
        raise ValidationFailed("This team mailbox is already in Mail.", code="MAIL_ALREADY_LINKED")
    now = datetime.now(UTC)
    account = MailAccount(address=box.address, display_name=box.sender_name, team_mailbox=name, created_by_admin_id=admin.id, created_at=now, updated_at=now)
    session.add(account)
    session.flush()
    session.add(MailAccess(account_id=account.id, admin_id=admin.id, can_send=True, can_manage=True, created_at=now))
    session.flush()
    return account


def update(session: Session, account: MailAccount, data: AccountIn) -> MailAccount:
    if account.team_mailbox:
        # Only what belongs to Mail; the servers and password are in Team → Mailboxes.
        account.display_name, account.language, account.color, account.sync_days = data.display_name, data.language, data.color, data.sync_days
    else:
        if data.imap_host != account.imap_host:
            check_host(data.imap_host)
        if data.smtp_host != account.smtp_host:
            check_host(data.smtp_host)
        for key, value in data.model_dump(exclude={"password"}).items():
            setattr(account, key, value)
        if data.password:
            account.password_enc = crypto.encrypt(data.password, associated_data=_aad(account.id))
    account.updated_at = datetime.now(UTC)
    if account.status == "error":
        account.status, account.last_error = "new", ""
    return account
