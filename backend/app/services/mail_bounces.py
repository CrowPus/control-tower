"""Returned e-mails: when an address does not exist (or the mailbox is full, or
the mail was refused), the receiving server sends a delivery report back to our
mailbox. Every few minutes the worker reads the new ones and marks the e-mail
`bounced` with the reason, so the page the person is on can say so and let them
correct the address (services/mail_delivery.py).

The mailbox is opened read-only. Of each new message only the sender, subject and
type are read; only delivery reports are opened, and only to find which of our
e-mails came back (its Message-ID) and why. Addresses in the reason are masked.
"""

from __future__ import annotations

import email
import imaplib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.parser import HeaderParser
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.logging import get_logger, log_event
from ..models import EmailDelivery, MailboxCursor

log = get_logger(__name__)

MAX_PER_SCAN = 200
_ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_MESSAGE_ID = re.compile(r"<[^<>\s]{6,200}@[^<>\s]{3,200}>")
_STATUS = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
_DSN_SENDER = re.compile(r"mailer-daemon|postmaster|mail delivery", re.IGNORECASE)
_DSN_SUBJECT = re.compile(r"undeliver|delivery (status|failure|failed|notification)|returned mail|unzustellbar|nicht zugestellt|zustellung fehlgeschlagen|failure notice", re.IGNORECASE)


@dataclass(frozen=True)
class Bounce:
    message_ids: tuple[str, ...]
    status: str  # "5.1.1"
    action: str  # failed | delayed | ...
    diagnostic: str

    @property
    def permanent(self) -> bool:
        return self.action != "delayed" and self.status.startswith("5")


def classify(status: str, diagnostic: str) -> str:
    """Why it came back, in words the pages translate."""
    text = diagnostic.lower()
    if status in ("5.1.1", "5.1.10", "5.1.2", "5.1.3", "5.1.6") or re.search(r"does not exist|no such (user|mailbox)|user unknown|unknown (user|recipient)|recipient address rejected|mailbox unavailable|not found", text):
        return "no_such_user"
    if status in ("5.2.2", "4.2.2") or re.search(r"quota|mailbox (is )?full|over quota|insufficient storage", text):
        return "mailbox_full"
    if status.startswith("5.7") or re.search(r"spam|blocked|blacklist|policy|denied|not authorized", text):
        return "blocked"
    return "rejected"


def mask(text: str) -> str:
    return _ADDRESS.sub("<address>", text)


def parse(raw: bytes) -> Bounce | None:
    """A delivery report, or None if this is not one."""
    msg = email.message_from_bytes(raw)
    action, status, diagnostic = "", "", ""
    ids: list[str] = []
    texts: list[str] = []
    for part in msg.walk():
        kind = part.get_content_type()
        if kind == "message/delivery-status":
            for block in part.get_payload() if isinstance(part.get_payload(), list) else []:
                if isinstance(block, Message):
                    action = action or str(block.get("Action", "")).strip().lower()
                    status = status or str(block.get("Status", "")).strip()
                    diagnostic = diagnostic or str(block.get("Diagnostic-Code", "")).split(";", 1)[-1].strip()
        elif kind == "message/rfc822":
            inner = part.get_payload()
            for item in inner if isinstance(inner, list) else [inner]:
                if isinstance(item, Message) and item.get("Message-ID"):
                    ids.append(str(item["Message-ID"]).strip())
        elif kind == "text/rfc822-headers":
            headers = HeaderParser().parsestr(_text(part))
            if headers.get("Message-ID"):
                ids.append(str(headers["Message-ID"]).strip())
        elif kind == "text/plain":
            texts.append(_text(part))
    body = "\n".join(texts)
    looks_like_report = msg.get_content_type() == "multipart/report" or bool(_DSN_SENDER.search(str(msg.get("From", "")))) or bool(_DSN_SUBJECT.search(str(msg.get("Subject", ""))))
    if not looks_like_report:
        return None
    if not status:
        found = _STATUS.search(body)
        status = ".".join(found.groups()) if found else ("5.0.0" if re.search(r"\b5\d\d\b", body) else "")
    if not diagnostic:
        line = next((ln.strip() for ln in body.splitlines() if re.search(r"\b[45]\d\d[ -]", ln)), "")
        diagnostic = line
    specific = _STATUS.search(diagnostic) or _STATUS.search(body)
    if status.endswith(".0.0") and specific and specific.group(0) != status:
        status = ".".join(specific.groups())  # "5.0.0" from the relay, "5.1.1" from the receiving server
    if not ids:
        ids = [m for m in _MESSAGE_ID.findall(body) if m.strip("<>") != str(msg.get("Message-ID", "")).strip("<>")]
    if not status:
        return None
    return Bounce(message_ids=tuple(dict.fromkeys(ids)), status=status, action=action or ("delayed" if status.startswith("4") else "failed"), diagnostic=mask(diagnostic)[:300])


def _text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return str(part.get_payload())


def apply(session: Session, bounce: Bounce) -> EmailDelivery | None:
    """Mark the e-mail it reports on; None if it is not one of ours or only delayed."""
    if not bounce.permanent or not bounce.message_ids:
        return None
    row = session.scalars(select(EmailDelivery).where(EmailDelivery.message_id.in_(bounce.message_ids))).first()
    if row is None or row.status == "bounced":
        return None
    now = datetime.now(UTC)
    row.status, row.bounce_reason, row.bounce_detail = "bounced", classify(bounce.status, bounce.diagnostic), f"{bounce.status} {bounce.diagnostic}".strip()[:300]
    row.settled_at, row.payload_encrypted, row.next_attempt_at, row.updated_at = now, "", None, now
    log_event(log, "e-mail returned", kind=row.kind, reason=row.bounce_reason, status=bounce.status)
    if row.kind == "employer_confirm":
        # A company that never gets its link is a customer lost without anyone noticing.
        from . import bells
        bells.alert_admins(f"A new company's confirmation e-mail came back ({row.bounce_reason})", "/email-deliveries")
    return row


class Mailbox(Protocol):
    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[Any]]: ...
    def response(self, code: str) -> tuple[str, list[Any]]: ...
    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]: ...
    def logout(self) -> Any: ...


def imap_host() -> str:
    cfg = settings()
    if cfg.mail_imap_host:
        return cfg.mail_imap_host
    return re.sub(r"^smtp\.", "imap.", cfg.smtp_host) if cfg.smtp_host.startswith("smtp.") else ""


def _connect() -> Mailbox | None:
    cfg = settings()
    host = imap_host()
    if not host or not cfg.smtp_login or not cfg.smtp_password:
        return None
    client = imaplib.IMAP4_SSL(host, cfg.mail_imap_port, timeout=20)
    client.login(cfg.smtp_login, cfg.smtp_password)
    return client


def scan(session: Session, *, connect: Callable[[], Mailbox | None] = _connect) -> dict[str, int | str]:
    """Worker (SYSTEM): read the mailbox's new messages for delivery reports."""
    try:
        box = connect()
    except (OSError, imaplib.IMAP4.error) as exc:
        log_event(log, "returned-mail check could not open the mailbox", error=type(exc).__name__)
        return {"skipped": "unavailable"}
    if box is None:
        return {"skipped": "not configured"}
    name = f"{settings().smtp_login}@{imap_host()}/INBOX"
    cursor = session.scalars(select(MailboxCursor).where(MailboxCursor.mailbox == name)).first()
    if cursor is None:
        cursor = MailboxCursor(mailbox=name, uid_validity=0, last_uid=0)
        session.add(cursor)
    counts: dict[str, int | str] = {"read": 0, "reports": 0, "bounced": 0}
    try:
        box.select("INBOX", readonly=True)
        validity = int((box.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
        if validity != cursor.uid_validity:
            # A new or rebuilt mailbox: start with the last three days, never its whole history.
            since = (datetime.now(UTC) - timedelta(days=3)).strftime("%d-%b-%Y")
            uids = _uids(box, "SINCE", since)
            cursor.uid_validity, cursor.last_uid = validity, (min(uids) - 1 if uids else 0)
        uids = [u for u in _uids(box, "UID", f"{cursor.last_uid + 1}:*") if u > cursor.last_uid][:MAX_PER_SCAN]
        for uid in uids:
            counts["read"] = int(counts["read"]) + 1
            head = _fetch(box, uid, "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT CONTENT-TYPE)]")
            headers = HeaderParser().parsestr(head.decode("utf-8", "replace"))
            if not (_DSN_SENDER.search(headers.get("From", "")) or _DSN_SUBJECT.search(headers.get("Subject", "")) or "multipart/report" in headers.get("Content-Type", "")):
                cursor.last_uid = uid
                continue
            bounce = parse(_fetch(box, uid, "BODY.PEEK[]"))
            if bounce is not None:
                counts["reports"] = int(counts["reports"]) + 1
                if apply(session, bounce) is not None:
                    counts["bounced"] = int(counts["bounced"]) + 1
            cursor.last_uid = uid
    finally:
        cursor.scanned_at = datetime.now(UTC)
        try:
            box.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    return counts


def _uids(box: Mailbox, *criteria: str) -> list[int]:
    typ, data = box.uid("SEARCH", *criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    return sorted(int(x) for x in data[0].split())


def _fetch(box: Mailbox, uid: int, what: str) -> bytes:
    typ, data = box.uid("FETCH", str(uid), f"({what})")
    for item in data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""
