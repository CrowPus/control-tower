"""Mailboxes the team uses (platform setting `mailboxes`, Admin → Team → Mailboxes).

The owner enters the server names, ports, login and sender in the admin app; nothing is in
code or `.env`. The password is stored encrypted (core/crypto.py, AES-GCM, key from
ENCRYPTION_SECRET) and never shown again: the admin app only learns whether one is set.

Shape: {name: {address, sender_name, login, password_enc, smtp_host, smtp_port,
smtp_security, imap_host, imap_port}}. In stage A1 there is one mailbox, `marketing`.
"""

from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core import crypto
from ...email.smtp import SmtpConfig
from .. import platform

NAMES = ("marketing",)
Security = Literal["ssl", "starttls"]


class Mailbox(BaseModel):
    address: str = ""
    sender_name: str = ""
    login: str = ""
    smtp_host: str = ""
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Security | None = None
    imap_host: str = ""
    imap_port: int | None = Field(default=None, ge=1, le=65535)


class MailboxView(Mailbox):
    name: str
    password_set: bool
    missing: list[str]


def _all(session: Session) -> dict[str, Any]:
    value = platform.get(session, "mailboxes") or {}
    return value if isinstance(value, dict) else {}


def _aad(name: str) -> str:
    return f"mailbox:{name}"


def missing(box: Mailbox, password_set: bool) -> list[str]:
    labels = {"address": "Address", "login": "Login", "smtp_host": "SMTP server", "smtp_port": "SMTP port",
              "smtp_security": "SMTP security", "imap_host": "IMAP server", "imap_port": "IMAP port"}
    out = [label for field, label in labels.items() if getattr(box, field) in ("", None)]
    if not password_set:
        out.append("Password")
    return out


def view(session: Session, name: str) -> MailboxView:
    raw = dict(_all(session).get(name) or {})
    box = Mailbox.model_validate({k: v for k, v in raw.items() if k in Mailbox.model_fields})
    password_set = bool(raw.get("password_enc"))
    return MailboxView(name=name, password_set=password_set, missing=missing(box, password_set), **box.model_dump())


def save(session: Session, name: str, box: Mailbox, *, password: str | None) -> MailboxView:
    """Store the settings. `password`: None keeps the stored one, "" removes it."""
    if name not in NAMES:
        raise ValueError(f"Unknown mailbox: {name}")
    stored = _all(session)
    current = dict(stored.get(name) or {})
    entry: dict[str, Any] = {**box.model_dump(), "password_enc": current.get("password_enc", "")}
    if password is not None:
        entry["password_enc"] = crypto.encrypt(password, associated_data=_aad(name)) if password else ""
    platform.put(session, "mailboxes", {**stored, name: entry})
    return view(session, name)


def secret(session: Session, name: str) -> str:
    token = str((_all(session).get(name) or {}).get("password_enc") or "")
    return crypto.decrypt(token, associated_data=_aad(name)) if token else ""


def smtp_config(session: Session, name: str) -> SmtpConfig | None:
    box = view(session, name)
    if box.missing:
        return None
    return SmtpConfig(host=box.smtp_host, port=int(box.smtp_port or 0), username=box.login, password=secret(session, name),
                      from_address=box.address, use_tls=box.smtp_security == "starttls")


def test(session: Session, name: str) -> dict[str, str]:
    """Sign in to both servers without sending or reading anything: "ok" or the reason."""
    box = view(session, name)
    if box.missing:
        return {"smtp": "Not set: " + ", ".join(box.missing), "imap": "Not set: " + ", ".join(box.missing)}
    password = secret(session, name)
    context = ssl.create_default_context()
    out: dict[str, str] = {}
    try:
        server = (smtplib.SMTP_SSL(box.smtp_host, int(box.smtp_port or 0), timeout=20, context=context) if box.smtp_security == "ssl"
                  else smtplib.SMTP(box.smtp_host, int(box.smtp_port or 0), timeout=20))
        with server:
            server.ehlo()
            if box.smtp_security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            server.login(box.login, password)
        out["smtp"] = "ok"
    except smtplib.SMTPAuthenticationError:
        out["smtp"] = "The server refused the login or password."
    except (OSError, smtplib.SMTPException) as exc:
        out["smtp"] = f"Could not connect: {exc.__class__.__name__}"
    try:
        client = imaplib.IMAP4_SSL(box.imap_host, int(box.imap_port or 0), timeout=20, ssl_context=context)
        try:
            client.login(box.login, password)
            client.select("INBOX", readonly=True)
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
        out["imap"] = "ok"
    except imaplib.IMAP4.error:
        out["imap"] = "The server refused the login or password."
    except OSError as exc:
        out["imap"] = f"Could not connect: {exc.__class__.__name__}"
    return out


# --- a copy in the mailbox's Sent folder ----------------------------------------------------------------
SENT_NAMES = ("Sent", "Gesendet", "Gesendete Objekte", "Gesendete Elemente", "Sent Items", "Sent Messages", "INBOX.Sent", "INBOX/Sent")


def _sent_folder(client: Any) -> str | None:
    """The folder the server marks as Sent (RFC 6154), else one with a usual name."""
    status, rows = client.list()
    if status != "OK":
        return None
    names: list[tuple[str, str]] = []
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        match = re.match(r'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if match:
            names.append((match.group("flags"), match.group("name").strip().strip('"')))
    for flags, name in names:
        if "\\Sent" in flags:
            return name
    by_name = {name.lower(): name for _flags, name in names}
    return next((by_name[n.lower()] for n in SENT_NAMES if n.lower() in by_name), None)


def keep_sent(session: Session, name: str, raw: bytes, *, connect: Callable[[], Any] | None = None) -> str:
    """Put a copy of a mail sent through this mailbox into its Sent folder, so the owner sees it in their
    mail program too. Best effort: the folder it went to, or "" when that was not possible."""
    box = view(session, name)
    if box.missing:
        return ""
    try:
        client = connect() if connect else imaplib.IMAP4_SSL(box.imap_host, int(box.imap_port or 0), timeout=20, ssl_context=ssl.create_default_context())
        if not connect:
            client.login(box.login, secret(session, name))
        try:
            folder = _sent_folder(client)
            if not folder:
                return ""
            status, _ = client.append(f'"{folder}"', "\\Seen", imaplib.Time2Internaldate(time.time()), raw)
            return folder if status == "OK" else ""
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    except (OSError, imaplib.IMAP4.error):
        return ""
