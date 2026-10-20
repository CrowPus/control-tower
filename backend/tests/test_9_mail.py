"""Mail: the team's own e-mail program in the admin app.

An in-memory mail server stands in for IMAP here (the same Connection the real one uses); the last test
runs against a real Dovecot when MAIL_TEST_IMAP=host:port is set (docker run dovecot/dovecot with
USER_PASSWORD), so the server's own answers are parsed too."""

from __future__ import annotations

import email
import os
import ssl
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db import transaction
from app.models import (
    AgentTask,
    MailAccount,
    MailDraft,
    MailFolder,
    MailMessage,
    MailOutbox,
    MailUpload,
)
from app.services.webmail import accounts, imap, outgoing, parse, sync, utf7
from app.services.webmail import tasks as mail_tasks
from tests.test_9_team import admin  # noqa: F401 - the owner signed in to the admin app (fixture)


def _db() -> Any:
    return transaction(write=True)


# --- an IMAP server in memory --------------------------------------------------------------------------------
@dataclass
class Box:
    validity: int
    next_uid: int = 1
    messages: dict[int, tuple[set[str], bytes, datetime]] = field(default_factory=dict)


class FakeServer:
    def __init__(self) -> None:
        self.boxes: dict[str, Box] = {}
        self.logins = 0
        for name in ("INBOX", "Sent", "Drafts", "Papierkorb", "Spam"):
            self.boxes[name] = Box(validity=1000 + len(self.boxes))

    def deliver(self, folder: str, raw: bytes, *, flags: set[str] | None = None, when: datetime | None = None) -> int:
        box = self.boxes[folder]
        uid = box.next_uid
        box.next_uid += 1
        box.messages[uid] = (set(flags or ()), raw, when or datetime.now(UTC))
        return uid


class FakeConnection(imap.Connection):
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.selected = None
        self.last_used = 0.0
        self.capabilities = {"MOVE", "UIDPLUS"}
        self.client = None

    def _box(self) -> Box:
        assert self.selected is not None
        return self.server.boxes[self.selected[0]]

    def folders(self) -> list[imap.Folder]:
        return [imap.Folder(name=n, delimiter="/", flags={"\\HasNoChildren"} | ({"\\Trash"} if n == "Papierkorb" else set())) for n in self.server.boxes]

    def status(self, name: str) -> dict[str, int]:
        box = self.server.boxes[name]
        return {"MESSAGES": len(box.messages), "UIDNEXT": box.next_uid, "UIDVALIDITY": box.validity,
                "UNSEEN": sum(1 for f, _r, _d in box.messages.values() if "\\Seen" not in f)}

    def select(self, name: str, *, readonly: bool = True) -> int:
        self.selected = (name, readonly)
        return len(self.server.boxes[name].messages)

    def search(self, *criteria: str) -> list[int]:
        box = self._box()
        if criteria[0] == "SINCE":
            since = datetime.strptime(criteria[1], "%d-%b-%Y").replace(tzinfo=UTC)
            return sorted(u for u, (_f, _r, d) in box.messages.items() if d >= since)
        if criteria[0] == "BEFORE":
            before = datetime.strptime(criteria[1], "%d-%b-%Y").replace(tzinfo=UTC)
            return sorted(u for u, (_f, _r, d) in box.messages.items() if d < before)
        if criteria[0] == "UID":
            lo, hi = criteria[1].split(":")
            top = max(box.messages) if box.messages else 0
            high = top if hi == "*" else int(hi)
            found = [u for u in box.messages if int(lo) <= u <= high]
            return sorted(found or ([top] if hi == "*" and top else []))
        return sorted(box.messages)

    def fetch(self, uids: list[int], items: str) -> list[imap.Fetched]:
        box = self._box()
        out = []
        for uid in uids:
            if uid in box.messages:
                flags, raw, when = box.messages[uid]
                body = raw if "BODY.PEEK[]" in items else (raw.split(b"\r\n\r\n")[0] if "HEADER" in items else None)
                out.append(imap.Fetched(uid=uid, flags=set(flags), internal_date=when, size=len(raw), body=body))
        return out

    def fetch_range(self, first_uid: int, items: str) -> list[imap.Fetched]:
        return self.fetch([u for u in self._box().messages if u >= first_uid], items)

    def store(self, uids: list[int], change: str, flags: str) -> None:
        box = self._box()
        for uid in uids:
            current = box.messages[uid][0]
            if change.startswith("+"):
                current |= set(flags.split())
            else:
                current -= set(flags.split())

    def move(self, uids: list[int], target: str) -> dict[int, int]:
        box, dest = self._box(), self.server.boxes[target]
        mapping = {}
        for uid in uids:
            flags, raw, when = box.messages.pop(uid)
            mapping[uid] = self.server.deliver(target, raw, flags=flags, when=when)
        assert dest
        return mapping

    def delete_messages(self, uids: list[int]) -> None:
        for uid in uids:
            self._box().messages.pop(uid, None)

    def append(self, folder: str, flags: str, raw: bytes) -> int | None:
        return self.server.deliver(folder, raw, flags=set(flags.split()))

    def create(self, name: str) -> None:
        self.server.boxes[name] = Box(validity=5000 + len(self.server.boxes))

    def rename(self, old: str, new: str) -> None:
        for name in [n for n in self.server.boxes if n == old or n.startswith(old + "/")]:
            self.server.boxes[new + name[len(old):]] = self.server.boxes.pop(name)

    def delete_folder(self, name: str) -> None:
        del self.server.boxes[name]

    def noop(self) -> bool:
        return True

    def logout(self) -> None:
        pass


def mail(*, subject: str = "Ihre Anfrage", sender: str = "Kim Muster <kim@bau.example>", to: str = "team@tower.example", text: str = "Hallo Team",
         html: str = "", when: datetime | None = None, message_id: str = "", in_reply_to: str = "", attachment: tuple[str, bytes] | None = None) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, to, subject
    m["Date"] = format_datetime(when or datetime.now(UTC))
    m["Message-ID"] = message_id or make_msgid(domain="bau.example")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(text)
    if html:
        m.add_alternative(html, subtype="html")
    if attachment:
        m.add_attachment(attachment[1], maintype="application", subtype="pdf", filename=attachment[0])
    return m.as_bytes().replace(b"\n", b"\r\n")


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    fake = FakeServer()
    monkeypatch.setattr(imap, "connector", lambda _server: FakeConnection(fake))
    monkeypatch.setattr(settings(), "webmail_allow_private_hosts", True)
    imap._pool.clear()
    yield fake
    imap._pool.clear()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], bytes]]:
    out: list[tuple[str, list[str], bytes]] = []
    monkeypatch.setattr(outgoing, "deliver", lambda _server, sender, to, raw: out.append((sender, to, raw)))
    return out


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with _db() as db:
        for model in (MailUpload, MailOutbox, MailDraft, MailMessage, MailFolder, MailAccount):
            db.execute(delete(model))
        db.execute(delete(AgentTask).where(AgentTask.task_type.like("mail.%")))


ACCOUNT = {"address": "team@tower.example", "display_name": "Tower Team", "login": "team@tower.example", "password": "secret-pw",
           "imap_host": "imap.tower.example", "imap_port": 993, "imap_security": "ssl", "smtp_host": "smtp.tower.example", "smtp_port": 465,
           "smtp_security": "ssl", "save_sent_copy": True, "language": "de", "sync_days": 90}


def _sync(account_id: int) -> dict[str, Any]:
    with _db() as db:
        account = db.get(MailAccount, account_id)
        assert account is not None
        return sync.sync_account(db, account)


def _add(admin: Any) -> dict[str, Any]:  # noqa: F811 - the fixture
    r = admin.post("/admin-api/mail/accounts", json=ACCOUNT)
    assert r.status_code == 201, r.text
    return r.json()


def _folder(account: dict[str, Any], role: str) -> dict[str, Any]:
    return next(f for f in account["folders"] if f["role"] == role)


# --- the basics -----------------------------------------------------------------------------------------
def test_folder_names_and_html_are_handled_safely() -> None:
    assert utf7.decode("Entw&APw-rfe") == "Entwürfe" and utf7.encode("Entwürfe") == "Entw&APw-rfe" and utf7.decode(utf7.encode("A&B/Öl")) == "A&B/Öl"
    shown = parse.clean_for_display('<p onclick="x()" style="color:red">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">j</a>'
                                    '<img src="cid:logo"><img src="data:text/html;base64,PHNjcmlwdD4="><form><input name=x></form><iframe src="https://x"></iframe>')
    assert "onclick" not in shown and "<script" not in shown and "javascript:" not in shown and "<form" not in shown and "<iframe" not in shown
    assert 'src="cid:logo"' in shown and "data:text/html" not in shown and 'style="color:red"' in shown
    assert 'target="_blank"' in parse.clean_for_display('<a href="https://www.example.com">x</a>')
    assert parse.normalise_subject("AW: Re: WG: Angebot") == "Angebot"
    assert imap._uid_set([1, 2, 3, 7, 9, 10]) == "1:3,7,9:10"
    raw = mail(html="<p>Guten <b>Tag</b></p>", attachment=("angebot.pdf", b"%PDF-1.4 test"))
    parsed = parse.parse(raw)
    assert parsed.from_address == "kim@bau.example" and parsed.subject == "Ihre Anfrage" and parsed.attachments[0]["filename"] == "angebot.pdf"
    assert parse.part_of(raw, parsed.attachments[0]["index"]) == ("angebot.pdf", "application/pdf", b"%PDF-1.4 test")


def test_fetch_answers_are_read_whatever_order_the_server_uses() -> None:
    rows = imap.parse_fetch([(b'1 (UID 7 FLAGS (\\Seen) INTERNALDATE "06-Oct-2026 09:12:00 +0200" RFC822.SIZE 12 BODY[] {12}', b"Subject: x\r\n\r\n"), b")",
                             (b"2 (BODY[] {3}", b"abc"), b' UID 9 FLAGS (\\Flagged))', b"3 (UID 11 FLAGS ())"])
    assert [(r.uid, r.flags, r.body) for r in rows] == [(7, {"\\Seen"}, b"Subject: x\r\n\r\n"), (9, {"\\Flagged"}, b"abc"), (11, set(), None)]
    assert rows[0].internal_date == datetime(2026, 10, 6, 7, 12, tzinfo=UTC)
    folders = imap.parse_list([b'(\\HasNoChildren \\Sent) "/" "Gesendete Objekte"', b'(\\Noselect) "/" "[Gmail]"', b'(\\HasNoChildren) "." INBOX',
                               (b'(\\HasNoChildren) "/" {8}', b"Entw\xc3\xbcrfe")])
    assert [(f.name, f.delimiter) for f in folders] == [("Gesendete Objekte", "/"), ("[Gmail]", "/"), ("INBOX", "."), ("Entwürfe", "/")]


def test_private_mail_servers_are_refused() -> None:
    with pytest.raises(Exception, match="not on the internet"):
        accounts.check_host("localhost")
    with pytest.raises(Exception, match="not on the internet"):
        accounts.check_host("127.0.0.1")


# --- an account, its folders and mail ---------------------------------------------------------------------
def test_an_account_is_added_mirrored_and_read(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    first = server.deliver("INBOX", mail(subject="Frage zu den Preisen", html='<p>Hallo <img src="https://tracker.example/p.gif"></p>', message_id="<q1@bau.example>"))
    server.deliver("INBOX", mail(subject="Alt", when=datetime.now(UTC) - timedelta(days=200)), when=datetime.now(UTC) - timedelta(days=200))
    server.deliver("INBOX", mail(subject="Mit Anhang", attachment=("angebot.pdf", b"%PDF-1.4 x")), flags={"\\Seen"})
    account = _add(admin)
    assert account["can_send"] and account["can_manage"] and account["password_set"] and "password" not in account
    with _db() as db:
        stored = db.get(MailAccount, account["id"])
        assert stored is not None and stored.password_enc and "secret-pw" not in stored.password_enc, "the password is kept encrypted"
    assert _sync(account["id"])["new"] == 2, "mail older than the sync days stays on the server"
    overview = admin.get("/admin-api/mail").json()
    account = overview["accounts"][0]
    assert [f["role"] for f in account["folders"]][:3] == ["inbox", "drafts", "sent"] and _folder(account, "trash")["name"] == "Papierkorb"
    assert _folder(account, "junk")["name"] == "Spam" and overview["unread"] == 2, "the server's own count: the old mail is unread too"
    inbox = _folder(account, "inbox")
    page = admin.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).json()
    assert [m["subject"] for m in page["items"]] == ["Mit Anhang", "Frage zu den Preisen"] and page["has_older_on_server"]
    detail = admin.get(f"/admin-api/mail/messages/{page['items'][1]['id']}").json()
    assert detail["remote_images"] and "tracker.example" in detail["html"] and detail["from_address"] == "kim@bau.example"
    shown = admin.get(f"/admin-api/mail/messages/{detail['id']}/frame")
    policy = shown.headers["content-security-policy"]
    assert "tracker.example" in shown.text and "img-src 'self' data:;" in policy and "script" not in policy.replace("default-src 'none'", "")
    assert "frame-ancestors 'self'" in policy and shown.headers["x-frame-options"] == "SAMEORIGIN", "framed by the admin only"
    assert "https:" in admin.get(f"/admin-api/mail/messages/{detail['id']}/frame", params={"images": "true"}).headers["content-security-policy"]
    assert admin.get("/admin-api/mail").headers["x-frame-options"] == "DENY", "everything else stays unframeable"
    with_file = admin.get(f"/admin-api/mail/messages/{page['items'][0]['id']}").json()
    assert with_file["has_attachments"] and with_file["attachments"][0]["filename"] == "angebot.pdf"
    download = admin.get(with_file["attachments"][0]["url"])
    assert download.status_code == 200 and download.content == b"%PDF-1.4 x" and "attachment" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert admin.post(f"/admin-api/mail/folders/{inbox['id']}/older").json() == {"stored": 1}, "older mail on request"
    # Read and flagged here: on the server at once.
    assert admin.post("/admin-api/mail/messages/actions", json={"ids": [detail["id"]], "action": "read"}).status_code == 200
    assert admin.post("/admin-api/mail/messages/actions", json={"ids": [detail["id"]], "action": "flag"}).status_code == 200
    assert server.boxes["INBOX"].messages[first][0] >= {"\\Seen", "\\Flagged"}
    flagged = admin.get("/admin-api/mail/messages", params={"view": "flagged"}).json()["items"]
    assert [m["id"] for m in flagged] == [detail["id"]]
    # Read on the phone: the mirror follows.
    server.boxes["INBOX"].messages[first][0].discard("\\Flagged")
    with _db() as db:
        folder = db.get(MailFolder, inbox["id"])
        assert folder is not None
        folder.synced_at = datetime.now(UTC) - timedelta(hours=1)
    _sync(account["id"])
    assert admin.get(f"/admin-api/mail/messages/{detail['id']}").json()["flagged"] is False


def test_moving_archiving_junk_and_delete_go_to_the_server_and_can_be_undone(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    uid = server.deliver("INBOX", mail(subject="Bitte ablegen"))
    account = _add(admin)
    _sync(account["id"])
    account = admin.get("/admin-api/mail").json()["accounts"][0]
    inbox, trash = _folder(account, "inbox"), _folder(account, "trash")
    message_id = admin.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).json()["items"][0]["id"]
    archived = admin.post("/admin-api/mail/messages/actions", json={"ids": [message_id], "action": "archive"}).json()
    assert "Archive" in server.boxes and uid not in server.boxes["INBOX"].messages, "an Archive folder is made when the account has none"
    archive_id = archived["moved"][str(message_id)]
    assert admin.get(f"/admin-api/mail/messages/{message_id}").json()["folder_id"] == archive_id, "the message keeps its place (and its id)"
    undone = admin.post("/admin-api/mail/messages/actions", json={"ids": [message_id], "action": "move", "folder_id": inbox["id"]}).json()
    assert undone["moved"][str(message_id)] == inbox["id"] and len(server.boxes["INBOX"].messages) == 1
    admin.post("/admin-api/mail/messages/actions", json={"ids": [message_id], "action": "delete"})
    assert len(server.boxes["Papierkorb"].messages) == 1, "deleted mail goes to the trash first"
    assert admin.get("/admin-api/mail/messages", params={"folder_id": trash["id"]}).json()["items"][0]["id"] == message_id
    admin.post("/admin-api/mail/messages/actions", json={"ids": [message_id], "action": "delete"})
    assert server.boxes["Papierkorb"].messages == {} and admin.get(f"/admin-api/mail/messages/{message_id}").status_code == 404, "from the trash: gone for good"
    server.deliver("Spam", mail(subject="Gewinn"))
    _sync(account["id"])
    junk = _folder(account, "junk")
    spam = admin.get("/admin-api/mail/messages", params={"folder_id": junk["id"]}).json()["items"][0]["id"]
    admin.post("/admin-api/mail/messages/actions", json={"ids": [spam], "action": "not_junk"})
    assert len(server.boxes["INBOX"].messages) == 1
    assert admin.post(f"/admin-api/mail/folders/{inbox['id']}/empty").status_code in (400, 422), "only Trash and Junk are emptied"


def test_folders_are_made_renamed_and_removed_on_the_server(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    account = _add(admin)
    _sync(account["id"])
    made = admin.post(f"/admin-api/mail/accounts/{account['id']}/folders", json={"name": "Kunden"}).json()
    inner = admin.post(f"/admin-api/mail/accounts/{account['id']}/folders", json={"name": "Bäckerei", "parent_id": made["id"]}).json()
    assert "Kunden/B&AOQ-ckerei" in server.boxes and inner["path"] == ["Kunden", "Bäckerei"] and inner["name"] == "Bäckerei"
    assert admin.delete(f"/admin-api/mail/folders/{made['id']}").status_code in (400, 422), "folders inside it first"
    renamed = admin.patch(f"/admin-api/mail/folders/{made['id']}", json={"name": "Firmen"}).json()
    assert renamed["name"] == "Firmen" and "Firmen/B&AOQ-ckerei" in server.boxes
    folders = {f["id"]: f for f in admin.get("/admin-api/mail").json()["accounts"][0]["folders"]}
    assert folders[inner["id"]]["path"] == ["Firmen", "Bäckerei"], "a folder inside moves with it"
    assert admin.post(f"/admin-api/mail/accounts/{account['id']}/folders", json={"name": "a/b"}).status_code in (400, 422)
    inbox = _folder(admin.get("/admin-api/mail").json()["accounts"][0], "inbox")
    assert admin.delete(f"/admin-api/mail/folders/{inbox['id']}").status_code in (400, 422), "the inbox stays"


# --- writing ------------------------------------------------------------------------------------------------
def test_a_reply_quotes_signs_threads_sends_after_the_undo_window_and_is_filed(admin: Any, server: FakeServer, sent: list[Any]) -> None:  # noqa: F811
    uid = server.deliver("INBOX", mail(subject="Frage", sender="Kim Muster <kim@bau.example>", to="team@tower.example, ole@bau.example",
                                       text="Was kostet eine Anzeige?", message_id="<q1@bau.example>"))
    account = _add(admin)
    admin.put(f"/admin-api/mail/accounts/{account['id']}/signature", json={"signature_html": '<p>Owner<br>Tower</p><script>x</script>'})
    _sync(account["id"])
    inbox = _folder(admin.get("/admin-api/mail").json()["accounts"][0], "inbox")
    message_id = admin.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).json()["items"][0]["id"]
    draft = admin.post("/admin-api/mail/drafts", json={"mode": "reply_all", "message_id": message_id}).json()
    assert draft["subject"] == "AW: Frage" and draft["to"] == ["Kim Muster <kim@bau.example>", "ole@bau.example"], "everyone but ourselves"
    assert outgoing.recipients(["=?utf-8?q?Sabine_Kr=C3=A4mer?= <s@x.de>"]) == ["Sabine Krämer <s@x.de>"]
    assert outgoing.recipients(['"Krämer, Sabine" <s@x.de>']) == ['"Krämer, Sabine" <s@x.de>'], "names are shown as people read them"
    assert "Am " in draft["html"] and "schrieb Kim Muster" in draft["html"] and "Owner" in draft["html"] and "<script" not in draft["html"]
    body = '<p>Die Veröffentlichung ist <b>kostenlos</b>.</p>' + draft["html"]
    admin.put(f"/admin-api/mail/drafts/{draft['id']}", json={**draft, "html": body, "bcc": ["chef@tower.example"]})
    upload = admin.post(f"/admin-api/mail/drafts/{draft['id']}/files", files={"file": ("preise.pdf", b"%PDF-1.4 preise", "application/pdf")})
    assert upload.status_code == 201, upload.text
    refused = admin.post(f"/admin-api/mail/drafts/{draft['id']}/files", files={"file": ("tool.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert refused.status_code in (400, 422), "programs are not attached"
    queued = admin.post(f"/admin-api/mail/drafts/{draft['id']}/send", json={"undo_seconds": 10}).json()
    assert queued["status"] == "queued" and admin.get(f"/admin-api/mail/drafts/{draft['id']}").status_code == 404
    # Undo within the window: back as the draft it was, with its file.
    back = admin.post(f"/admin-api/mail/outbox/{queued['id']}/cancel").json()
    assert back["subject"] == "AW: Frage" and back["uploads"][0]["filename"] == "preise.pdf" and "kostenlos" in back["html"]
    queued = admin.post(f"/admin-api/mail/drafts/{back['id']}/send", json={"undo_seconds": 0}).json()
    with _db() as db:
        task = db.execute(select(AgentTask).where(AgentTask.task_type == "mail.send").order_by(AgentTask.id.desc())).scalars().first()
        assert task is not None and task.payload == {"outbox_id": queued["id"]}
        job = SimpleNamespace(payload=dict(task.payload))
    mail_tasks.mail_send(job)  # type: ignore[arg-type]
    sender, to, raw = sent[0]
    assert sender == "team@tower.example" and sorted(to) == ["chef@tower.example", "kim@bau.example", "ole@bau.example"]
    out = email.message_from_bytes(raw)
    assert out["In-Reply-To"] == "<q1@bau.example>" and "<q1@bau.example>" in out["References"] and out["Bcc"] is None, "Bcc is never in the mail itself"
    assert out["From"] == "Tower Team <team@tower.example>" and out.is_multipart()
    names = [p.get_filename() for p in out.walk() if p.get_filename()]
    assert names == ["preise.pdf"]
    kinds = [p.get_content_type() for p in out.walk()]
    assert "text/plain" in kinds and "text/html" in kinds
    copy = next(iter(server.boxes["Sent"].messages.values()))
    assert b"Bcc: chef@tower.example" in copy[1] and "\\Seen" in copy[0], "the Sent copy keeps the Bcc, like Outlook"
    assert "\\Answered" in server.boxes["INBOX"].messages[uid][0], "the answered mail is marked answered"
    assert admin.get(f"/admin-api/mail/outbox/{queued['id']}").json()["status"] == "sent"
    with _db() as db:
        assert db.execute(select(MailUpload)).first() is None, "files are not kept once the mail went out"


def test_drafts_live_in_the_drafts_folder_and_forwarding_takes_the_files(admin: Any, server: FakeServer, sent: list[Any]) -> None:  # noqa: F811
    server.deliver("INBOX", mail(subject="Angebot", attachment=("angebot.pdf", b"%PDF-1.4 a")))
    account = _add(admin)
    _sync(account["id"])
    inbox = _folder(admin.get("/admin-api/mail").json()["accounts"][0], "inbox")
    message_id = admin.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).json()["items"][0]["id"]
    forward = admin.post("/admin-api/mail/drafts", json={"mode": "forward", "message_id": message_id}).json()
    assert forward["subject"] == "WG: Angebot" and forward["to"] == [] and forward["uploads"][0]["filename"] == "angebot.pdf"
    assert "Weitergeleitete Nachricht" in forward["html"]
    saved = admin.put(f"/admin-api/mail/drafts/{forward['id']}", params={"server": "true"}, json={**forward, "to": ["chef@tower.example"]}).json()
    assert saved["on_server"] and len(server.boxes["Drafts"].messages) == 1
    draft_raw = next(iter(server.boxes["Drafts"].messages.values()))
    assert b"X-Tower-Draft: " in draft_raw[1] and "\\Draft" in draft_raw[0]
    admin.put(f"/admin-api/mail/drafts/{forward['id']}", params={"server": "true"}, json={**forward, "to": ["chef@tower.example"], "subject": "WG: Angebot!"})
    assert len(server.boxes["Drafts"].messages) == 1, "the copy is replaced, not added to"
    assert [d["subject"] for d in admin.get("/admin-api/mail/drafts").json()] == ["WG: Angebot!"]
    queued = admin.post(f"/admin-api/mail/drafts/{forward['id']}/send", json={}).json()
    with _db() as db:
        row = db.get(MailOutbox, queued["id"])
        assert row is not None
        outgoing.send(db, row)
    assert server.boxes["Drafts"].messages == {}, "sent: the draft's server copy goes"
    assert [p.get_filename() for p in email.message_from_bytes(sent[0][2]).walk() if p.get_filename()] == ["angebot.pdf"]
    # A draft written on the phone is finished here.
    server.deliver("Drafts", mail(subject="Vom Handy", sender="team@tower.example", to="kunde@firma.example", text="Entwurf"), flags={"\\Draft"})
    _sync(account["id"])
    drafts_folder = _folder(admin.get("/admin-api/mail").json()["accounts"][0], "drafts")
    phone = admin.get("/admin-api/mail/messages", params={"folder_id": drafts_folder["id"]}).json()["items"][0]
    assert phone["is_draft"]
    opened = admin.post(f"/admin-api/mail/messages/{phone['id']}/edit-draft").json()
    assert opened["to"] == ["kunde@firma.example"] and opened["subject"] == "Vom Handy" and opened["on_server"]


def test_scheduled_mail_waits_and_a_failure_is_shown_and_retried(admin: Any, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    account = _add(admin)
    _sync(account["id"])
    draft = admin.post("/admin-api/mail/drafts", json={"account_id": account["id"], "mode": "new"}).json()
    assert admin.post(f"/admin-api/mail/drafts/{draft['id']}/send", json={}).status_code in (400, 422), "a recipient first"
    admin.put(f"/admin-api/mail/drafts/{draft['id']}", json={**draft, "to": ["kein-adresse"]})
    assert "not an e-mail address" in admin.post(f"/admin-api/mail/drafts/{draft['id']}/send", json={}).text
    admin.put(f"/admin-api/mail/drafts/{draft['id']}", json={**draft, "to": ["kunde@firma.example"], "subject": "Morgen"})
    later = datetime.now(UTC) + timedelta(hours=12)
    queued = admin.post(f"/admin-api/mail/drafts/{draft['id']}/send", json={"send_at": later.isoformat()}).json()
    with _db() as db:
        task = db.execute(select(AgentTask).where(AgentTask.task_type == "mail.send")).scalars().first()
        assert task is not None and abs((task.run_at - later).total_seconds()) < 2
    assert admin.get("/admin-api/mail/outbox").json()[0]["status"] == "queued"

    def refuse(*_args: Any) -> None:
        import smtplib

        raise smtplib.SMTPAuthenticationError(535, b"no")

    monkeypatch.setattr(outgoing, "deliver", refuse)
    with _db() as db:
        row = db.get(MailOutbox, queued["id"])
        assert row is not None
        row.send_at = datetime.now(UTC)
        outgoing.send(db, row)
    failed = admin.get(f"/admin-api/mail/outbox/{queued['id']}").json()
    assert failed["status"] == "failed" and "refused the login" in failed["error"]
    assert admin.post(f"/admin-api/mail/outbox/{queued['id']}/retry").json()["status"] == "queued"


# --- the team ------------------------------------------------------------------------------------------------
def test_only_who_has_access_sees_an_account_and_read_only_cannot_send(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    from app.main import app as admin_app
    from tests.test_9_admin_dashboard import AdminClient, _fresh_code

    server.deliver("INBOX", mail(subject="Vertraulich"))
    account = _add(admin)
    _sync(account["id"])
    email_address = f"mail-support-{uuid4().hex[:6]}@tower.example"
    invited = admin.post("/admin-api/admins", json={"email": email_address, "full_name": "Sam Support", "role": "support"}).json()
    token = parse_qs(urlparse(invited["link"]).query)["token"][0]
    with AdminClient(admin_app) as c:
        c.post("/admin-api/auth/accept-invite", json={"token": token, "password": "support-passphrase-2026"})
        c.csrf = c.post("/admin-api/auth/login", json={"email": email_address, "password": "support-passphrase-2026"}).json()["csrf_token"]
        secret = c.post("/admin-api/auth/enroll/start").json()["secret"]
        assert c.post("/admin-api/auth/enroll/confirm", json={"code": _fresh_code(secret, email_address)}).status_code == 200
        assert c.get("/admin-api/mail").json()["accounts"] == [], "no access, nothing to see"
        inbox = _folder(admin.get("/admin-api/mail").json()["accounts"][0], "inbox")
        message_id = admin.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).json()["items"][0]["id"]
        assert c.get(f"/admin-api/mail/messages/{message_id}").status_code == 404
        assert c.get("/admin-api/mail/messages", params={"folder_id": inbox["id"]}).status_code == 404
        assert c.get(f"/admin-api/mail/accounts/{account['id']}/access").status_code == 404
        sam = next(p for p in admin.get("/admin-api/mail/people").json() if p["email"] == email_address)
        me = next(x for x in admin.get(f"/admin-api/mail/accounts/{account['id']}/access").json())
        access = [{"admin_id": me["admin_id"], "can_send": True, "can_manage": True}, {"admin_id": sam["id"], "can_send": False, "can_manage": False}]
        assert admin.put(f"/admin-api/mail/accounts/{account['id']}/access", json=access).status_code == 200
        seen = c.get("/admin-api/mail").json()["accounts"][0]
        assert seen["can_send"] is False and seen["can_manage"] is False and seen["imap_host"] == "", "server settings only for who manages it"
        assert c.get(f"/admin-api/mail/messages/{message_id}").json()["subject"] == "Vertraulich"
        assert c.post("/admin-api/mail/drafts", json={"mode": "reply", "message_id": message_id}).status_code == 403, "read only"
        assert c.put(f"/admin-api/mail/accounts/{account['id']}", json={**ACCOUNT, "password": None}).status_code == 403
        assert c.delete(f"/admin-api/mail/accounts/{account['id']}").status_code == 403
    log = admin.get("/admin-api/audit-log", params={"limit": 50})
    if log.status_code == 200:
        assert any(e.get("action") == "mail.access_changed" for e in log.json().get("items", log.json()) if isinstance(e, dict))


def test_the_marketing_mailbox_is_linked_not_copied(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    from app.services import platform
    from app.services.team import mailboxes

    with _db() as db:
        mailboxes.save(db, "marketing", mailboxes.Mailbox(address="marketing@tower.example", sender_name="Acme", login="marketing@tower.example",
                                                          smtp_host="smtp.tower.example", smtp_port=465, smtp_security="ssl", imap_host="imap.tower.example",
                                                          imap_port=993), password="pw")  # noqa: S106 - test only
    try:
        assert admin.get("/admin-api/mail").json()["team_mailboxes"] == ["marketing"]
        linked = admin.post("/admin-api/mail/accounts/team/marketing").json()
        assert linked["team_mailbox"] == "marketing" and linked["address"] == "marketing@tower.example" and linked["imap_host"] == ""
        with _db() as db:
            row = db.get(MailAccount, linked["id"])
            assert row is not None and row.password_enc == "", "the password stays in Team → Mailboxes"
            assert accounts.imap_server(db, row).password == "pw"  # noqa: S105 - test only
        assert admin.get("/admin-api/mail").json()["team_mailboxes"] == []
    finally:
        with _db() as db:
            platform.put(db, "mailboxes", {})


def test_addresses_are_suggested_from_mail_and_our_company_list(admin: Any, server: FakeServer) -> None:  # noqa: F811 - the fixture
    server.deliver("INBOX", mail(sender="Kim Muster <kim@bau.example>"))
    account = _add(admin)
    _sync(account["id"])
    found = admin.get("/admin-api/mail/contacts", params={"q": "kim"}).json()
    assert found[0]["address"] == "kim@bau.example" and found[0]["name"] == "Kim Muster"
    assert admin.get("/admin-api/mail/contacts", params={"q": "team@"}).json() == [], "our own address is not suggested"
    search = admin.get("/admin-api/mail/messages", params={"q": "from:kim is:unread"}).json()["items"]
    assert len(search) == 1


# --- a real IMAP server --------------------------------------------------------------------------------------
@pytest.mark.skipif(not os.environ.get("MAIL_TEST_IMAP"), reason="MAIL_TEST_IMAP=host:port of a Dovecot test server")
def test_against_a_real_imap_server(monkeypatch: pytest.MonkeyPatch) -> None:
    host, port = os.environ["MAIL_TEST_IMAP"].split(":")
    monkeypatch.setattr(settings(), "webmail_allow_private_hosts", True)

    def unverified() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    monkeypatch.setattr(imap, "ssl_context", unverified)
    user = f"u{uuid4().hex[:8]}"
    server = accounts.Server(host, int(port), "ssl", user, os.environ.get("MAIL_TEST_PASSWORD", "pass"))
    conn = imap.open_connection(server)
    for name in ("Sent", "Drafts", "Trash", utf7.encode("Entwürfe alt")):
        conn.create(name)
    for i in range(3):
        conn.append("INBOX", "", mail(subject=f"Nachricht {i} – Grüße", html=f"<p>Hallo {i}</p>", attachment=("a.pdf", b"%PDF x") if i == 2 else None))
    conn.logout()
    now = datetime.now(UTC)
    with _db() as db:
        account = MailAccount(address=f"{user}@example.org", login=user, imap_host=host, imap_port=int(port), smtp_host=host, created_at=now, updated_at=now)
        db.add(account)
        db.flush()
        from app.core import crypto

        account.password_enc = crypto.encrypt(server.password, associated_data=f"mail_account:{account.id}")
        result = sync.sync_account(db, account)
        assert result.get("new") == 3, result
        folders = {f.display_name: f for f in db.execute(select(MailFolder).where(MailFolder.account_id == account.id)).scalars()}
        assert {"INBOX", "Sent", "Drafts", "Trash", "Entwürfe alt"} <= set(folders) and folders["Trash"].role == "trash"
        rows = list(db.execute(select(MailMessage).where(MailMessage.folder_id == folders["INBOX"].id).order_by(MailMessage.uid)).scalars())
        assert rows[0].subject == "Nachricht 0 – Grüße" and "Hallo 0" in rows[0].html and rows[2].has_attachments
        with imap.connection(accounts.imap_server(db, account)) as live:
            live.select("INBOX", readonly=False)
            live.store([rows[0].uid], "+FLAGS.SILENT", "\\Seen")
            mapping = live.move([rows[1].uid], "Trash")
            assert len(mapping) == 1, "the server says where it went (UIDPLUS)"
            new_uid = live.append("Drafts", "\\Draft \\Seen", mail(subject="Entwurf"))
            assert new_uid
        folders["INBOX"].synced_at = None
        sync.sync_account(db, account)
        db.refresh(rows[0])
        assert rows[0].seen
        assert db.execute(select(MailMessage).where(MailMessage.folder_id == folders["Trash"].id)).scalars().first() is not None
        assert db.get(MailMessage, rows[1].id) is None, "moved elsewhere: gone from the inbox's mirror"
        db.execute(delete(MailMessage).where(MailMessage.account_id == account.id))
        db.execute(delete(MailFolder).where(MailFolder.account_id == account.id))
        db.delete(account)
