"""Writing and sending: drafts (new, reply, reply all, forward) with the writer's
signature, files and pictures in the text, the copy in the account's Drafts folder, and the outbox: a
mail waits there for its undo window (or the time it was scheduled for), then goes out over the
account's SMTP server, a copy lands in its Sent folder, the answered mail is marked answered and the
draft's server copy is removed.
"""

from __future__ import annotations

import base64
import binascii
import re
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid
from html import escape
from pathlib import Path
from typing import Any, cast

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...core import clock
from ...core.errors import NotFound, ValidationFailed
from ...core.sanitize import sanitize_header_field
from ...models import (
    AdminUser,
    MailAccess,
    MailAccount,
    MailDraft,
    MailFolder,
    MailMessage,
    MailOutbox,
    MailUpload,
)
from ..scanner import EXECUTABLE_SIGNATURES, antivirus
from . import accounts, imap, ops, parse

# Safety rails, not business values: what most providers accept in one mail, recipients per mail, and
# the longest undo window offered.
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 25 * 1024 * 1024
MAX_RECIPIENTS = 100
MAX_UNDO = 60
BLOCKED = {".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1", ".hta", ".cpl", ".jar", ".lnk", ".reg"}
STYLE = "font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#1f2937"

PREFIX = {"de": {"reply": "AW: ", "forward": "WG: "}, "en": {"reply": "Re: ", "forward": "Fwd: "}}


class Refused(ValidationFailed):
    """The mail or file cannot go out as it is; the reason is shown."""


# --- addresses -------------------------------------------------------------------------------------------
def _fmt(pair: list[str]) -> str:
    """"Kim Krämer <kim@x.de>" as people read it (the mail's headers are encoded when it is built)."""
    name, address = (pair + ["", ""])[:2]
    name = sanitize_header_field(name).replace('"', "'").strip()
    if not name:
        return address
    return f'"{name}" <{address}>' if any(c in name for c in ',;:<>@()[]\\') else f"{name} <{address}>"


def recipients(values: list[str]) -> list[str]:
    """Typed addresses ("Kim <kim@x.de>; ole@y.de") as checked, one per entry."""
    out: list[str] = []
    for name, address in getaddresses([v for v in values if v]):
        if not address.strip():
            continue
        try:
            checked = validate_email(address.strip(), check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise Refused(f"This is not an e-mail address: {address}", code="MAIL_BAD_ADDRESS") from exc
        out.append(_fmt([parse.decode_value(name), checked]))
    return out


def bare(address: str) -> str:
    return (getaddresses([address])[0][1] if address else "").lower()


# --- the draft as it starts --------------------------------------------------------------------------------
def _when(row: MailMessage, language: str) -> str:
    moment = (row.sent_at or row.received_at).astimezone(clock.zone())
    return moment.strftime("%d.%m.%Y um %H:%M") if language == "de" else moment.strftime("%d %b %Y at %H:%M")


def _who(name: str, address: str) -> str:
    return escape(f"{name} <{address}>" if name else address)


def _original_html(row: MailMessage) -> str:
    return parse.clean_for_sending(row.html) if row.html else parse.text_to_html(row.text)


def signature_block(signature_html: str) -> str:
    return f'<div data-signature="1">{parse.clean_for_sending(signature_html)}</div>' if signature_html.strip() else ""


def quote(row: MailMessage, language: str, mode: str) -> str:
    if mode == "forward":
        label = {"de": ("Weitergeleitete Nachricht", "Betreff", "Datum", "Von", "An", "Cc"),
                 "en": ("Forwarded message", "Subject", "Date", "From", "To", "Cc")}[language if language in ("de", "en") else "de"]
        lines = [f"-------- {label[0]} --------", f"{label[1]}: {escape(row.subject)}", f"{label[2]}: {_when(row, language)}",
                 f"{label[3]}: {_who(row.from_name, row.from_address)}", f"{label[4]}: {escape(', '.join(_fmt(p) for p in row.to))}"]
        if row.cc:
            lines.append(f"{label[5]}: {escape(', '.join(_fmt(p) for p in row.cc))}")
        return '<div><br></div><div>' + "<br>".join(lines) + "</div><br>" + _original_html(row)
    head = (f"Am {_when(row, language)} schrieb {_who(row.from_name, row.from_address)}:" if language == "de"
            else f"On {_when(row, language)}, {_who(row.from_name, row.from_address)} wrote:")
    return (f'<div><br></div><div>{head}</div><blockquote style="margin:0 0 0 .8ex;border-left:2px solid #c7d2fe;padding-left:1ex">'
            f"{_original_html(row)}</blockquote>")


def _subject(subject: str, mode: str, language: str) -> str:
    base = parse.normalise_subject(subject)
    prefix = PREFIX.get(language, PREFIX["de"])["forward" if mode == "forward" else "reply"]
    return (prefix + base)[:1000]


def start(session: Session, admin: AdminUser, account: MailAccount, access: MailAccess, mode: str, source: MailMessage | None) -> MailDraft:
    now = datetime.now(UTC)
    draft = MailDraft(account_id=account.id, admin_id=admin.id, mode=mode, source_message_id=source.id if source else None, to=[], cc=[], bcc=[],
                      subject="", html="", created_at=now, updated_at=now)
    signature = signature_block(access.signature_html)
    body = "<div><br></div>" + (f"<div><br></div>{signature}" if signature else "")
    if source is not None and mode in ("reply", "reply_all", "forward"):
        own = sync_own(session, account)
        draft.subject = _subject(source.subject, mode, account.language)
        if mode != "forward":
            first = source.reply_to or [[source.from_name, source.from_address]]
            to = [p for p in first if p[1].lower() not in own] or first
            draft.to = [_fmt(p) for p in to]
            if mode == "reply_all":
                seen = {p[1].lower() for p in to} | own
                more = [p for p in source.to if p[1].lower() not in seen]
                draft.to += [_fmt(p) for p in more]
                seen |= {p[1].lower() for p in more}
                draft.cc = [_fmt(p) for p in source.cc if p[1].lower() not in seen]
            draft.in_reply_to = source.message_id
            draft.references = (" ".join([*source.references.split()[-20:], source.message_id])).strip()[-5000:]
        body += quote(source, account.language, mode)
    draft.html = body
    session.add(draft)
    session.flush()
    return draft


def sync_own(session: Session, account: MailAccount) -> set[str]:
    return {accounts.sender(session, account)[1].lower(), account.address.lower()}


def copy_attachments(session: Session, admin: AdminUser, draft: MailDraft, source: MailMessage, *, inline: bool = False) -> int:
    """A forwarded mail's files (and, for an edited server draft, its pictures) go with it."""
    wanted = [a for a in source.attachments if inline or not a.get("inline")]
    if not wanted:
        return 0
    raw = ops.raw_message(session, admin, source)
    count = 0
    for attachment in wanted:
        part = parse.part_of(raw, int(attachment["index"]))
        if part is None or not part[2]:
            continue
        filename, mime, data = part
        session.add(MailUpload(admin_id=admin.id, draft_id=draft.id, filename=filename[:255], mime_type=mime[:120], size=len(data), data=data,
                               content_id=str(attachment.get("cid") or "") if attachment.get("inline") else "", scan="forwarded", created_at=datetime.now(UTC)))
        count += 1
    session.flush()
    return count


def from_server_draft(session: Session, admin: AdminUser, message: MailMessage) -> MailDraft:
    """A draft saved on the phone or in webmail, opened here to finish (its server copy is replaced)."""
    if message.draft_ref:
        mine = session.execute(select(MailDraft).where(MailDraft.id == message.draft_ref, MailDraft.admin_id == admin.id)).scalar_one_or_none()
        if mine:
            return mine
    now = datetime.now(UTC)
    draft = MailDraft(account_id=message.account_id, admin_id=admin.id, mode="new", to=[_fmt(p) for p in message.to], cc=[_fmt(p) for p in message.cc],
                      bcc=[_fmt(p) for p in message.bcc], subject=message.subject, html=_original_html(message), in_reply_to=message.in_reply_to,
                      references=message.references, server_uid=message.uid, server_folder_id=message.folder_id, created_at=now, updated_at=now)
    session.add(draft)
    session.flush()
    copy_attachments(session, admin, draft, message, inline=True)
    return draft


# --- files ----------------------------------------------------------------------------------------------
def add_upload(session: Session, admin: AdminUser, draft: MailDraft, filename: str, data: bytes, content_type: str, *, inline: bool = False) -> MailUpload:
    name = Path(filename or "file").name.replace("\\", "_")[:200] or "file"
    if Path(name).suffix.lower() in BLOCKED:
        raise Refused("Programs and scripts cannot be attached.", code="MAIL_FILE_BLOCKED")
    if not data:
        raise Refused("The file is empty.", code="MAIL_FILE_EMPTY")
    if len(data) > MAX_FILE:
        raise Refused(f"A file may be at most {MAX_FILE // (1024 * 1024)} MB.", code="MAIL_FILE_TOO_LARGE")
    total = sum(u.size for u in session.execute(select(MailUpload).where(MailUpload.draft_id == draft.id)).scalars())
    if total + len(data) > MAX_TOTAL:
        raise Refused(f"All files of one mail may be at most {MAX_TOTAL // (1024 * 1024)} MB together.", code="MAIL_FILES_TOO_LARGE")
    if any(data.startswith(signature) for signature, _ in EXECUTABLE_SIGNATURES):
        raise Refused("Programs and scripts cannot be attached.", code="MAIL_FILE_BLOCKED")
    verdict = scan(data)
    mime = (content_type or "application/octet-stream").split(";")[0].strip().lower()[:120]
    if inline and mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise Refused("Only PNG, JPEG, GIF and WebP pictures go into the text.", code="MAIL_FILE_NOT_IMAGE")
    upload = MailUpload(admin_id=admin.id, draft_id=draft.id, filename=name, mime_type=mime if "/" in mime else "application/octet-stream", size=len(data), data=data,
                        content_id=f"{uuid.uuid4().hex}@tower" if inline else "", scan=verdict, created_at=datetime.now(UTC))
    session.add(upload)
    session.flush()
    return upload


def scan(data: bytes) -> str:
    verdict = antivirus(data)
    if verdict == "infected":
        raise Refused("The virus scanner rejected this file.", code="MAIL_FILE_INFECTED")
    if verdict == "unavailable":
        raise Refused("The virus scanner cannot be reached just now; try again in a moment.", code="MAIL_SCANNER_UNAVAILABLE")
    if verdict == "off" and settings().execution_mode != "test":
        raise Refused("No virus scanner is set up, so files cannot be attached or opened.", code="MAIL_SCANNER_OFF")
    return verdict


# --- the mail as it goes out --------------------------------------------------------------------------------
_DATA_IMG = re.compile(r'src="data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=\s]+)"', re.IGNORECASE)


def build(*, sender_name: str, sender_address: str, to: list[str], cc: list[str], bcc: list[str], subject: str, html: str, in_reply_to: str = "",
          references: str = "", uploads: list[MailUpload] | None = None, message_id: str = "", draft_ref: int | None = None,
          keep_bcc: bool = False) -> EmailMessage:
    """The mail with HTML and the same as plain text, pictures in the text as related parts, files attached."""
    body = parse.clean_for_sending(html)
    pictures: list[tuple[str, str, bytes]] = []

    def lift(match: re.Match[str]) -> str:
        try:
            data = base64.b64decode(re.sub(r"\s", "", match.group(2)), validate=True)
        except (ValueError, binascii.Error):
            return 'src=""'
        cid = f"{uuid.uuid4().hex}@tower"
        pictures.append((cid, match.group(1).lower().replace("jpg", "jpeg"), data))
        return f'src="cid:{cid}"'

    body = _DATA_IMG.sub(lift, body)
    message = EmailMessage()
    message["From"] = formataddr((sanitize_header_field(sender_name), sender_address)) if sender_name else sender_address
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc and keep_bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = sanitize_header_field(subject)[:900]
    message["Date"] = formatdate(localtime=False)
    domain = sender_address.rsplit("@", 1)[-1] or "localhost"
    message["Message-ID"] = message_id or make_msgid(idstring=uuid.uuid4().hex[:12], domain=domain)
    if in_reply_to:
        message["In-Reply-To"] = sanitize_header_field(in_reply_to)[:300]
    if references:
        message["References"] = sanitize_header_field(references)[-2000:]
    if draft_ref is not None:
        message["X-Tower-Draft"] = str(draft_ref)
    message.set_content(parse.html_to_text(body) or " ")
    message.add_alternative(f'<!doctype html><html><head><meta charset="utf-8"></head><body><div style="{STYLE}">{body}</div></body></html>', subtype="html")
    html_part = cast(EmailMessage, message.get_payload()[1])  # type: ignore[index]
    files = uploads or []
    for cid, mime, data in pictures:
        html_part.add_related(data, maintype="image", subtype=mime.split("/")[1], cid=f"<{cid}>")
    for upload in files:
        if upload.content_id and f"cid:{upload.content_id}" in body:
            maintype, _, subtype = upload.mime_type.partition("/")
            html_part.add_related(upload.data, maintype=maintype, subtype=subtype or "octet-stream", cid=f"<{upload.content_id}>", filename=upload.filename)
    for upload in files:
        if upload.content_id and f"cid:{upload.content_id}" in body:
            continue
        maintype, _, subtype = upload.mime_type.partition("/")
        if maintype == "message" and subtype == "rfc822":
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(upload.data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=sanitize_header_field(upload.filename)[:200])
    return message


def _uploads(session: Session, *, draft_id: int | None = None, outbox_id: int | None = None) -> list[MailUpload]:
    stmt = select(MailUpload).where(MailUpload.draft_id == draft_id) if draft_id is not None else select(MailUpload).where(MailUpload.outbox_id == outbox_id)
    return list(session.execute(stmt.order_by(MailUpload.id)).scalars())


# --- the copy in the Drafts folder ----------------------------------------------------------------------------
def _ensure(session: Session, account: MailAccount, conn: imap.Connection, role: str) -> MailFolder:
    existing = ops.folder_by_role(session, account.id, role)
    if existing:
        return existing
    name = {"drafts": "Drafts", "sent": "Sent"}[role]
    row = session.execute(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == name)).scalar_one_or_none()
    if row is None:
        conn.create(name)
        row = MailFolder(account_id=account.id, name=name, display_name=name, delimiter="/", role=role)
        session.add(row)
    row.role = role
    session.flush()
    return row


def _remove_server_copy(session: Session, conn: imap.Connection, folder_id: int | None, uid: int | None) -> None:
    if not folder_id or not uid:
        return
    folder = session.get(MailFolder, folder_id)
    if folder is None:
        return
    conn.select(folder.name, readonly=False)
    conn.delete_messages([uid])
    for row in session.execute(select(MailMessage).where(MailMessage.folder_id == folder.id, MailMessage.uid == uid)).scalars():
        session.delete(row)


def save_to_server(session: Session, admin: AdminUser, draft: MailDraft) -> None:
    """Keep the draft in the account's Drafts folder too (so it is on the phone), replacing the last copy."""
    account = session.get(MailAccount, draft.account_id)
    if account is None:
        return
    name, address = accounts.sender(session, account)
    message = build(sender_name=name, sender_address=address, to=_lenient(draft.to), cc=_lenient(draft.cc), bcc=_lenient(draft.bcc), subject=draft.subject,
                    html=draft.html, in_reply_to=draft.in_reply_to, references=draft.references, uploads=_uploads(session, draft_id=draft.id),
                    draft_ref=draft.id, keep_bcc=True)
    with imap.connection(accounts.imap_server(session, account)) as conn:
        folder = _ensure(session, account, conn, "drafts")
        uid = conn.append(folder.name, "\\Draft \\Seen", message.as_bytes())
        _remove_server_copy(session, conn, draft.server_folder_id, draft.server_uid)
    draft.server_uid, draft.server_folder_id = uid, folder.id
    ops.request_sync(session, account.id, folder.id)


def _lenient(values: list[str]) -> list[str]:
    try:
        return recipients(values)
    except Refused:
        return []


def discard_server_copy(session: Session, draft: MailDraft) -> None:
    """The draft moves to another account: its copy in the old account's Drafts folder goes."""
    account = session.get(MailAccount, draft.account_id)
    if account is not None and draft.server_uid:
        try:
            with imap.connection(accounts.imap_server(session, account)) as conn:
                _remove_server_copy(session, conn, draft.server_folder_id, draft.server_uid)
        except (imap.MailServerError, ValidationFailed):
            pass
    draft.server_uid = draft.server_folder_id = None


def discard(session: Session, admin: AdminUser, draft: MailDraft) -> None:
    discard_server_copy(session, draft)
    for upload in _uploads(session, draft_id=draft.id):
        session.delete(upload)
    session.delete(draft)


# --- the outbox -------------------------------------------------------------------------------------------
def queue(session: Session, admin: AdminUser, draft: MailDraft, *, undo_seconds: int = 0, send_at: datetime | None = None) -> MailOutbox:
    account, _access = accounts.require(session, admin, draft.account_id, send=True)
    to, cc, bcc = recipients(draft.to), recipients(draft.cc), recipients(draft.bcc)
    if not (to or cc or bcc):
        raise Refused("Add at least one recipient.", code="MAIL_NO_RECIPIENT")
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise Refused(f"At most {MAX_RECIPIENTS} recipients per mail.", code="MAIL_TOO_MANY_RECIPIENTS")
    now = datetime.now(UTC)
    when = send_at.astimezone(UTC) if send_at else now + timedelta(seconds=max(0, min(undo_seconds, MAX_UNDO)))
    if send_at and send_at.astimezone(UTC) < now - timedelta(minutes=1):
        raise Refused("Choose a time in the future.", code="MAIL_SEND_AT_PAST")
    row = MailOutbox(account_id=account.id, admin_id=admin.id, mode=draft.mode, source_message_id=draft.source_message_id, to=to, cc=cc, bcc=bcc,
                     subject=draft.subject, html=parse.clean_for_sending(draft.html), in_reply_to=draft.in_reply_to, references=draft.references,
                     send_at=when, status="queued", draft_server_uid=draft.server_uid, draft_server_folder_id=draft.server_folder_id, created_at=now)
    session.add(row)
    session.flush()
    for upload in _uploads(session, draft_id=draft.id):
        upload.outbox_id, upload.draft_id = row.id, None
    session.delete(draft)
    session.flush()
    from ...workers import queue as q

    q.enqueue(session, "mail.send", {"outbox_id": row.id}, queue="notifications", priority=1, run_at=when, max_attempts=1, dedupe_key=f"mail.send:{row.id}")
    return row


def cancel(session: Session, admin: AdminUser, row: MailOutbox) -> MailDraft:
    """Undo: the mail is not sent and comes back as the draft it was."""
    if row.status not in ("queued", "failed"):
        raise Refused("This mail has gone out already.", code="MAIL_ALREADY_SENT")
    now = datetime.now(UTC)
    draft = MailDraft(account_id=row.account_id, admin_id=admin.id, mode=row.mode, source_message_id=row.source_message_id, to=row.to, cc=row.cc, bcc=row.bcc,
                      subject=row.subject, html=row.html, in_reply_to=row.in_reply_to, references=row.references, server_uid=row.draft_server_uid,
                      server_folder_id=row.draft_server_folder_id, created_at=now, updated_at=now)
    session.add(draft)
    session.flush()
    for upload in _uploads(session, outbox_id=row.id):
        upload.draft_id, upload.outbox_id = draft.id, None
    session.delete(row)
    session.flush()
    return draft


def _smtp(server: accounts.Server) -> smtplib.SMTP:
    context = imap.ssl_context()
    if server.security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(server.host, server.port, timeout=60, context=context)
    else:
        client = smtplib.SMTP(server.host, server.port, timeout=60)
        client.ehlo()
        client.starttls(context=context)
    client.ehlo()
    client.login(server.login, server.password)
    return client


def smtp_login(server: accounts.Server) -> None:
    """Sign in to the outgoing server and leave (testing an account's settings); ValidationFailed with the reason."""
    try:
        _smtp(server).quit()
    except smtplib.SMTPAuthenticationError as exc:
        raise Refused("The outgoing server refused the login or password.", code="MAIL_LOGIN_REFUSED") from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise Refused(f"Could not connect to {server.host}: {exc.__class__.__name__}", code="MAIL_SERVER_UNREACHABLE") from exc


# Tests give a fake here: (server, from address, recipients, raw message) → None.
def deliver(server: accounts.Server, sender_address: str, to: list[str], raw: bytes) -> None:
    with _smtp(server) as client:
        refused = client.sendmail(sender_address, to, raw)
    if refused and len(refused) == len(to):
        raise Refused("The server refused every recipient.", code="MAIL_RECIPIENTS_REFUSED")


def send(session: Session, row: MailOutbox) -> dict[str, Any]:
    """Send one mail of the outbox (the worker, when its time has come)."""
    account = session.get(MailAccount, row.account_id)
    if account is None:
        row.status, row.error = "failed", "The account was removed."
        return {"failed": "account"}
    name, address = accounts.sender(session, account)
    uploads = _uploads(session, outbox_id=row.id)
    message = build(sender_name=name, sender_address=address, to=row.to, cc=row.cc, bcc=row.bcc, subject=row.subject, html=row.html,
                    in_reply_to=row.in_reply_to, references=row.references, uploads=uploads)
    everyone = [bare(a) for a in [*row.to, *row.cc, *row.bcc]]
    try:
        deliver(accounts.smtp_server(session, account), address, everyone, message.as_bytes())
    except smtplib.SMTPAuthenticationError:
        row.status, row.error = "failed", "The outgoing server refused the login or password."
        return {"failed": "login"}
    except smtplib.SMTPRecipientsRefused as exc:
        row.status, row.error = "failed", f"The server refused the recipients: {', '.join(list(exc.recipients)[:3])}"[:300]
        return {"failed": "recipients"}
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        row.status, row.error = "failed", f"The outgoing server could not be reached or refused the mail ({exc.__class__.__name__})."[:300]
        return {"failed": "smtp"}
    except ValidationFailed as exc:
        row.status, row.error = "failed", exc.message[:300]
        return {"failed": "settings"}
    row.status, row.sent_at, row.message_id, row.error = "sent", datetime.now(UTC), str(message["Message-ID"]), ""
    # After it went out: best effort, never a reason to send again.
    try:
        with imap.connection(accounts.imap_server(session, account)) as conn:
            if account.save_sent_copy:
                copy = build(sender_name=name, sender_address=address, to=row.to, cc=row.cc, bcc=row.bcc, subject=row.subject, html=row.html,
                             in_reply_to=row.in_reply_to, references=row.references, uploads=uploads, message_id=row.message_id, keep_bcc=True)
                folder = _ensure(session, account, conn, "sent")
                conn.append(folder.name, "\\Seen", copy.as_bytes())
                row.sent_folder = folder.display_name
            source = session.get(MailMessage, row.source_message_id) if row.source_message_id else None
            if source is not None:
                folder_row = session.get(MailFolder, source.folder_id)
                if folder_row is not None:
                    conn.select(folder_row.name, readonly=False)
                    conn.store([source.uid], "+FLAGS.SILENT", "$Forwarded" if row.mode == "forward" else "\\Answered")
                    if row.mode == "forward":
                        source.forwarded = True
                    else:
                        source.answered = True
            _remove_server_copy(session, conn, row.draft_server_folder_id, row.draft_server_uid)
    except (imap.MailServerError, ValidationFailed):
        pass
    for upload in uploads:
        session.delete(upload)
    ops.request_sync(session, account.id)
    return {"sent": row.id}


def get_draft(session: Session, admin: AdminUser, draft_id: int) -> MailDraft:
    draft = session.get(MailDraft, draft_id)
    if draft is None or draft.admin_id != admin.id:
        raise NotFound("No such draft.", code="MAIL_DRAFT_NOT_FOUND")
    accounts.require(session, admin, draft.account_id)
    return draft
