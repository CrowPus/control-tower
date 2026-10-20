"""Every e-mail is recorded and followed (services/mail_delivery.py, mail_bounces.py):

- sent at once, recorded with its Message-ID; the content kept only while it waits;
- a busy mail server does not fail the sender: the e-mail is retried, then given up;
- a newer e-mail of the same kind supersedes one still waiting;
- delivery reports in the mailbox are recognised (standard reports and the plain
  "returning message to sender" kind), matched to the e-mail and classified, and
  nothing else in the mailbox is opened;
- the admin sees what failed or came back.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import TransientExternalError
from app.db import admin_transaction, transaction
from app.models import EmailDelivery
from app.services import mail_bounces, mail_delivery, system_mail


def _rows(owner: str) -> list[EmailDelivery]:
    with transaction() as db:
        rows = list(db.scalars(select(EmailDelivery).where(EmailDelivery.owner == owner).order_by(EmailDelivery.id)))
        db.expunge_all()
        return rows


def _send(owner: str | None = None) -> tuple[str, str]:
    """One admin invitation, recorded like every e-mail the install sends. Returns (owner, recipient)."""
    owner = owner or f"admin:{uuid4().hex[:8]}"
    email = f"deliv-{uuid4().hex[:8]}@example.com"
    with transaction(write=True) as db:
        system_mail.send("admin_invitation", to=email, name="Dora", locale="en", link="https://tower.example/accept-invite?token=x", session=db, owner=owner)
    return owner, email


def _dsn(message_id: str, *, status: str = "5.1.1", diagnostic: str = "smtp; 550 5.1.1 The email account that you tried to reach does not exist") -> bytes:
    """A standard delivery report (RFC 3464), as receiving servers send it."""
    return (
        "From: Mail Delivery System <MAILER-DAEMON@mout.example.net>\r\n"
        "To: info@tower.example\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        "Message-ID: <report-1@mout.example.net>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b1"\r\n\r\n'
        "--b1\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "This is the mail system. Your message could not be delivered to support@lalapinta.example.\r\n\r\n"
        "--b1\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mout.example.net\r\n\r\n"
        f"Final-Recipient: rfc822; support@lalapinta.example\r\nAction: {'delayed' if status.startswith('4') else 'failed'}\r\nStatus: {status}\r\nDiagnostic-Code: {diagnostic}\r\n\r\n"
        "--b1\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        f"From: Control Tower <info@tower.example>\r\nTo: support@lalapinta.example\r\nSubject: Your Control Tower admin account\r\nMessage-ID: {message_id}\r\n\r\n"
        "--b1--\r\n"
    ).encode()


def _plain_bounce(message_id: str) -> bytes:
    """The kind IONOS sent on 2 October 2026: plain text, the original quoted."""
    msg = EmailMessage()
    msg["From"] = "Mail Delivery System <mailer-daemon@kundenserver.de>"
    msg["Subject"] = "Mail delivery failed: returning message to sender"
    msg.set_content(
        "This message was created automatically by mail delivery software.\n\nA message that you sent could not be delivered to one or more of\n"
        "its recipients. This is a permanent error. The following address(es)\nfailed:\n\n  support@lalapinta.example:\n"
        "    host smtp.google.com [142.250.27.27]\n    SMTP error from remote server for RCPT TO command:\n"
        "    550-5.1.1 The email account that you tried to reach does not exist. Please\n    550 5.1.1 https://support.google.com/mail/?p=NoSuchUser\n\n"
        f"------ This is a copy of the message's headers. ------\n\nMessage-ID: {message_id}\nFrom: Control Tower <info@tower.example>\n"
    )
    return msg.as_bytes()


# --- recording, retries ---------------------------------------------------------------------------------------
def test_every_mail_is_recorded_with_its_message_id() -> None:
    owner, email = _send()
    rows = _rows(owner)
    assert [(r.kind, r.status, r.recipient) for r in rows] == [("admin_invitation", "sent", email)]
    assert re.fullmatch(r"<[^@]+@[\w.-]+>", rows[0].message_id) and rows[0].payload_encrypted == "", "content is not kept once sent"


def test_a_busy_mail_server_is_retried_then_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def busy(to: str, subject: str, text: str, html: str, *, message_id: str) -> None:
        calls.append(message_id)
        raise TransientExternalError("SMTP connection problem: TimeoutError", code="SMTP_UNAVAILABLE")

    monkeypatch.setattr(system_mail, "deliver", busy)
    owner, _email = _send()  # the sender does not fail
    row = _rows(owner)[-1]
    assert row.status == "retrying" and row.attempts == 1 and row.payload_encrypted and row.last_error == "SMTP_UNAVAILABLE"
    assert row.next_attempt_at is not None and row.next_attempt_at > datetime.now(UTC)

    sent: list[str] = []
    monkeypatch.setattr(system_mail, "deliver", lambda to, subject, text, html, *, message_id: sent.append(subject))
    with transaction(write=True) as db:
        db.get(EmailDelivery, row.id).next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)  # type: ignore[union-attr]
        assert mail_delivery.retry_due(db)["sent"] >= 1
    again = _rows(owner)[-1]
    assert again.status == "sent" and again.attempts == 2 and again.payload_encrypted == "" and sent and "admin" in sent[-1]

    monkeypatch.setattr(system_mail, "deliver", busy)
    owner2, _ = _send()
    with transaction(write=True) as db:
        stuck = db.scalars(select(EmailDelivery).where(EmailDelivery.owner == owner2)).one()
        stuck.attempts, stuck.next_attempt_at = len(mail_delivery.RETRY_AFTER), datetime.now(UTC) - timedelta(seconds=1)
        mail_delivery.retry_due(db)
    assert _rows(owner2)[-1].status == "failed", "given up after the last retry"


def test_a_newer_mail_supersedes_one_still_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_mail, "deliver", lambda *a, **k: (_ for _ in ()).throw(TransientExternalError("busy", code="SMTP_UNAVAILABLE")))
    owner, _email = _send()
    monkeypatch.undo()
    _send(owner)
    assert [r.status for r in _rows(owner)] == ["superseded", "sent"]


# --- returned e-mails -------------------------------------------------------------------------------------------
def test_reports_are_read_matched_and_classified() -> None:
    owner, _email = _send()
    row = _rows(owner)[-1]
    standard = mail_bounces.parse(_dsn(row.message_id))
    assert standard is not None and standard.status == "5.1.1" and row.message_id in standard.message_ids and standard.permanent
    assert "@" not in standard.diagnostic.replace("<address>", "")
    plain = mail_bounces.parse(_plain_bounce(row.message_id))
    assert plain is not None and plain.status == "5.1.1" and row.message_id in plain.message_ids
    with transaction(write=True) as db:
        assert mail_bounces.apply(db, plain) is not None
        assert mail_bounces.apply(db, plain) is None, "once"
    returned = _rows(owner)[-1]
    assert (returned.status, returned.bounce_reason) == ("bounced", "no_such_user")
    assert mail_bounces.classify("5.2.2", "") == "mailbox_full" and mail_bounces.classify("5.7.1", "message blocked") == "blocked"
    delayed = mail_bounces.parse(_dsn(row.message_id, status="4.4.1", diagnostic="smtp; 451 try again later"))
    assert delayed is not None and not delayed.permanent
    ordinary = EmailMessage()
    ordinary["From"] = "Kunde <kunde@example.com>"
    ordinary["Subject"] = "Frage zu Control Tower"
    ordinary.set_content("Hallo, eine Frage …")
    assert mail_bounces.parse(ordinary.as_bytes()) is None


class FakeMailbox:
    def __init__(self, messages: dict[int, bytes]) -> None:
        self.messages, self.opened = messages, []  # type: list[int]

    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[Any]]:
        assert readonly, "the mailbox is only ever read"
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str) -> tuple[str, list[Any]]:
        return code, [b"777"]

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in sorted(self.messages)).encode()]
        uid, what = int(args[0]), args[1]
        raw = self.messages[uid]
        if "HEADER.FIELDS" in what:
            return "OK", [(b"1", raw.split(b"\n\n", 1)[0] + b"\n\n")]
        self.opened.append(uid)
        return "OK", [(b"1", raw)]

    def logout(self) -> None:
        return None


def test_the_scan_opens_only_reports_and_reads_each_once() -> None:
    owner, _email = _send()
    row = _rows(owner)[-1]
    personal = EmailMessage()
    personal["From"] = "Kunde <kunde@example.com>"
    personal["Subject"] = "Vertraulich"
    personal.set_content("Bitte nicht lesen.")
    box = FakeMailbox({41: personal.as_bytes(), 42: _dsn(row.message_id)})
    with transaction(write=True) as db:
        assert mail_bounces.scan(db, connect=lambda: box)["bounced"] == 1
    assert box.opened == [42], "an ordinary message is never opened"
    with transaction(write=True) as db:
        assert mail_bounces.scan(db, connect=lambda: box)["read"] == 0, "the cursor remembers"
    assert _rows(owner)[-1].status == "bounced"


# --- what the admin sees and what is refused ------------------------------------------------------------------
def test_the_admin_sees_what_failed_or_came_back() -> None:
    from app.admin.routes import email_deliveries

    with admin_transaction() as db:
        out = email_deliveries(ctx=None, session=db)  # type: ignore[arg-type]
    assert out.counts.get("bounced", 0) >= 1
    assert all(item.status in ("failed", "bounced") for item in out.items)
    assert not hasattr(out.items[0], "payload_encrypted")
