"""Admin → Mail: the team's own e-mail program — accounts like in Outlook, folders,
reading, searching, writing with signature and files, drafts, the outbox with undo and scheduled send.

Everyone on the team sees only the accounts they were given access to. Adding, changing and removing an
account and who has access to it are in the admin log; what people read and write is not.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from ..core.errors import NotFound, ValidationFailed
from ..models import (
    AdminUser,
    MailAccess,
    MailAccount,
    MailDraft,
    MailFolder,
    MailMessage,
    MailOutbox,
    MailUpload,
)
from ..services.team import mailboxes
from ..services.webmail import accounts, imap, ops, outgoing, parse
from .auth import record
from .deps import AdminDb, CurrentAdmin

router = APIRouter(prefix="/admin-api/mail", tags=["admin-mail"])
PAGE = 50


# --- shapes ------------------------------------------------------------------------------------------------
class FolderOut(BaseModel):
    id: int
    account_id: int
    name: str
    path: list[str]
    role: str
    total: int
    unread: int
    has_older: bool


class AccountOut(BaseModel):
    id: int
    address: str
    display_name: str
    team_mailbox: str
    color: str
    status: str
    last_error: str
    last_sync_at: datetime | None
    language: str
    sync_days: int
    save_sent_copy: bool
    can_send: bool
    can_manage: bool
    signature_html: str
    # Server settings, for who manages the account (empty for a linked team mailbox).
    login: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_security: str = "ssl"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    password_set: bool = False
    folders: list[FolderOut]


class PresetOut(BaseModel):
    key: str
    label: str
    domains: list[str]
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    save_sent_copy: bool
    note: str


class MailOverview(BaseModel):
    accounts: list[AccountOut]
    presets: list[PresetOut]
    # Team mailboxes (Team → Mailboxes) that are set up and can be added with one click.
    team_mailboxes: list[str]
    drafts: int
    outbox: int
    unread: int


class MessageSummary(BaseModel):
    id: int
    account_id: int
    folder_id: int
    subject: str
    from_name: str
    from_address: str
    to: list[list[str]]
    preview: str
    received_at: datetime
    seen: bool
    flagged: bool
    answered: bool
    forwarded: bool
    has_attachments: bool
    is_draft: bool
    thread_key: str


class MessagePage(BaseModel):
    items: list[MessageSummary]
    next: str | None
    has_older_on_server: bool


class AttachmentOut(BaseModel):
    index: int
    filename: str
    mime: str
    size: int
    inline: bool
    url: str


class ThreadItem(MessageSummary):
    folder: str


class MessageDetail(MessageSummary):
    folder: str
    folder_role: str
    cc: list[list[str]]
    bcc: list[list[str]]
    reply_to: list[list[str]]
    sent_at: datetime | None
    html: str
    text: str
    body_state: str
    attachments: list[AttachmentOut]
    remote_images: bool
    list_unsubscribe: str
    thread: list[ThreadItem]
    can_send: bool


class UploadOut(BaseModel):
    id: int
    filename: str
    mime_type: str
    size: int
    content_id: str
    url: str


class DraftOut(BaseModel):
    id: int
    account_id: int
    mode: str
    source_message_id: int | None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    html: str
    uploads: list[UploadOut]
    on_server: bool
    updated_at: datetime


class DraftSummary(BaseModel):
    id: int
    account_id: int
    to: list[str]
    subject: str
    preview: str
    updated_at: datetime


class DraftIn(BaseModel):
    account_id: int | None = None
    to: list[str] = Field(default_factory=list, max_length=200)
    cc: list[str] = Field(default_factory=list, max_length=200)
    bcc: list[str] = Field(default_factory=list, max_length=200)
    subject: str = Field(default="", max_length=1000)
    html: str = Field(default="", max_length=3_000_000)


class DraftStartIn(BaseModel):
    account_id: int | None = None
    mode: Literal["new", "reply", "reply_all", "forward"] = "new"
    message_id: int | None = None
    to: list[str] = Field(default_factory=list, max_length=50)


class SendIn(BaseModel):
    undo_seconds: int = Field(default=0, ge=0, le=outgoing.MAX_UNDO)
    send_at: datetime | None = None


class OutboxOut(BaseModel):
    id: int
    account_id: int
    to: list[str]
    subject: str
    send_at: datetime
    status: str
    error: str
    sent_at: datetime | None
    sent_folder: str


class ActionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: ops.Action
    folder_id: int | None = None


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None


class FolderRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class AccessEntry(BaseModel):
    admin_id: int
    can_send: bool = True
    can_manage: bool = False


class AccessOut(AccessEntry):
    name: str
    email: str


class PersonOut(BaseModel):
    id: int
    name: str
    email: str


class SignatureIn(BaseModel):
    signature_html: str = Field(default="", max_length=200_000)


class ContactOut(BaseModel):
    name: str
    address: str


class TestOut(BaseModel):
    imap: str
    smtp: str


class TestIn(accounts.AccountIn):
    account_id: int | None = None


# --- helpers -----------------------------------------------------------------------------------------------
def _folder_out(f: MailFolder) -> FolderOut:
    path = f.display_name.split(f.delimiter) if f.delimiter else [f.display_name]
    if f.role == "inbox":
        path = ["Inbox"]
    return FolderOut(id=f.id, account_id=f.account_id, name=path[-1], path=path, role=f.role, total=f.total, unread=f.unread, has_older=f.has_older)


def _account_out(session: Session, admin: AdminUser, a: MailAccount, x: MailAccess | None) -> AccountOut:
    folders = sorted(session.execute(select(MailFolder).where(MailFolder.account_id == a.id)).scalars(),
                     key=lambda f: (ops_order(f.role), f.display_name.lower()))
    manage = admin.role == "owner" or bool(x and x.can_manage)
    out = AccountOut(id=a.id, address=a.address, display_name=a.display_name, team_mailbox=a.team_mailbox, color=a.color, status=a.status, last_error=a.last_error,
                     last_sync_at=a.last_sync_at, language=a.language, sync_days=a.sync_days, save_sent_copy=a.save_sent_copy, can_send=bool(x and x.can_send),
                     can_manage=manage, signature_html=x.signature_html if x else "", folders=[_folder_out(f) for f in folders] if x else [])
    if manage and not a.team_mailbox:
        out.login, out.imap_host, out.imap_port, out.imap_security = a.login, a.imap_host, a.imap_port, a.imap_security
        out.smtp_host, out.smtp_port, out.smtp_security, out.password_set = a.smtp_host, a.smtp_port, a.smtp_security, bool(a.password_enc)
    return out


def ops_order(role: str) -> int:
    return {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5}.get(role, 6)


def _summary(m: MailMessage) -> dict[str, Any]:
    return {"id": m.id, "account_id": m.account_id, "folder_id": m.folder_id, "subject": m.subject, "from_name": m.from_name, "from_address": m.from_address,
            "to": m.to, "preview": m.preview, "received_at": m.received_at, "seen": m.seen, "flagged": m.flagged, "answered": m.answered, "forwarded": m.forwarded,
            "has_attachments": m.has_attachments, "is_draft": m.is_draft, "thread_key": m.thread_key}


def _message(session: Session, admin: AdminUser, message_id: int) -> tuple[MailMessage, MailAccess]:
    row = session.get(MailMessage, message_id)
    if row is None:
        raise NotFound("No such message.", code="MAIL_MESSAGE_NOT_FOUND")
    _account, access = accounts.member(session, admin, row.account_id)
    return row, access


def _with_cids(m: MailMessage, html: str) -> str:
    for a in m.attachments:
        cid = str(a.get("cid") or "")
        if cid:
            html = html.replace(f"cid:{cid}", f"/admin-api/mail/messages/{m.id}/attachments/{a['index']}?inline=1")
    return html


_REMOTE = re.compile(r"""(src|background)\s*=\s*["']?\s*https?:|url\(\s*["']?\s*https?:""", re.IGNORECASE)


def _upload_out(u: MailUpload) -> UploadOut:
    return UploadOut(id=u.id, filename=u.filename, mime_type=u.mime_type, size=u.size, content_id=u.content_id, url=f"/admin-api/mail/uploads/{u.id}")


def _draft_out(session: Session, d: MailDraft) -> DraftOut:
    uploads = list(session.execute(select(MailUpload).where(MailUpload.draft_id == d.id).order_by(MailUpload.id)).scalars())
    return DraftOut(id=d.id, account_id=d.account_id, mode=d.mode, source_message_id=d.source_message_id, to=d.to, cc=d.cc, bcc=d.bcc, subject=d.subject,
                    html=d.html, uploads=[_upload_out(u) for u in uploads], on_server=bool(d.server_uid), updated_at=d.updated_at)


def _outbox_out(o: MailOutbox) -> OutboxOut:
    return OutboxOut(id=o.id, account_id=o.account_id, to=o.to, subject=o.subject, send_at=o.send_at, status=o.status, error=o.error, sent_at=o.sent_at,
                     sent_folder=o.sent_folder)


def _download(data: bytes, filename: str, mime: str, *, inline: bool = False) -> Response:
    safe = re.sub(r"[^\w.\- ]", "_", filename)[:150] or "file"
    disposition = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename[:150])}", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'", "Cache-Control": "private, max-age=600"}
    return Response(content=data, media_type=mime if "/" in mime else "application/octet-stream", headers=headers)


# --- accounts ----------------------------------------------------------------------------------------------
@router.get("", response_model=MailOverview)
def overview(ctx: CurrentAdmin, session: AdminDb) -> MailOverview:
    rows = accounts.visible(session, ctx.admin)
    ids = [a.id for a, _ in rows]
    linked = set(session.execute(select(MailAccount.team_mailbox).where(MailAccount.team_mailbox != "")).scalars())
    team = [n for n in mailboxes.NAMES if n not in linked and not mailboxes.view(session, n).missing] if ctx.admin.role == "owner" else []
    drafts = session.execute(select(func.count()).select_from(MailDraft).where(MailDraft.admin_id == ctx.admin.id)).scalar() or 0
    waiting = session.execute(select(func.count()).select_from(MailOutbox).where(MailOutbox.admin_id == ctx.admin.id, MailOutbox.status.in_(("queued", "sending", "failed")))).scalar() or 0
    unread = session.execute(select(func.coalesce(func.sum(MailFolder.unread), 0)).where(MailFolder.account_id.in_(ids), MailFolder.role == "inbox")).scalar() if ids else 0
    return MailOverview(accounts=[_account_out(session, ctx.admin, a, x) for a, x in rows], presets=[PresetOut(**p) for p in accounts.PRESETS], team_mailboxes=team,
                        drafts=int(drafts), outbox=int(waiting), unread=int(unread or 0))


@router.get("/unread")
def unread(ctx: CurrentAdmin, session: AdminDb) -> dict[str, int]:
    """New mail in the inboxes this admin may open (the menu's counter)."""
    ids = accounts.visible_ids(session, ctx.admin)
    total = session.execute(select(func.coalesce(func.sum(MailFolder.unread), 0)).where(MailFolder.account_id.in_(ids), MailFolder.role == "inbox")).scalar() if ids else 0
    return {"unread": int(total or 0)}


@router.post("/accounts/test", response_model=TestOut)
def test_account(data: TestIn, ctx: CurrentAdmin, session: AdminDb) -> TestOut:
    """Sign in to both servers with these settings, without saving, sending or reading anything."""
    password = data.password or ""
    if not password and data.account_id:
        account, _ = accounts.require(session, ctx.admin, data.account_id, manage=True)
        password = accounts.imap_server(session, account).password if not account.team_mailbox else ""
    out = {"imap": "ok", "smtp": "ok"}
    login = data.login or data.address
    try:
        accounts.check_host(data.imap_host)
        imap.open_connection(accounts.Server(data.imap_host, data.imap_port, data.imap_security, login, password)).logout()
    except (imap.MailServerError, ValidationFailed) as exc:
        out["imap"] = exc.message
    try:
        accounts.check_host(data.smtp_host)
        outgoing.smtp_login(accounts.Server(data.smtp_host, data.smtp_port, data.smtp_security, login, password))
    except ValidationFailed as exc:
        out["smtp"] = exc.message
    return TestOut(**out)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def add_account(data: accounts.AccountIn, ctx: CurrentAdmin, session: AdminDb) -> AccountOut:
    account = accounts.create(session, ctx.admin, data)
    record(session, ctx.admin, "mail.account_added", target_type="mail_account", target_id=account.id, ip=ctx.ip,
           metadata={"address": account.address, "imap": account.imap_host, "smtp": account.smtp_host})
    ops.request_sync(session, account.id)
    return _account_out(session, ctx.admin, account, accounts.access_of(session, account.id, ctx.admin.id))


@router.post("/accounts/team/{name}", response_model=AccountOut, status_code=201)
def link_team(name: str, ctx: CurrentAdmin, session: AdminDb) -> AccountOut:
    if ctx.admin.role != "owner":
        raise NotFound("No such team mailbox.", code="MAIL_TEAM_MAILBOX_NOT_FOUND")
    if name not in mailboxes.NAMES:
        raise NotFound("No such team mailbox.", code="MAIL_TEAM_MAILBOX_NOT_FOUND")
    account = accounts.link_team_mailbox(session, ctx.admin, name)
    record(session, ctx.admin, "mail.account_added", target_type="mail_account", target_id=account.id, ip=ctx.ip, metadata={"team_mailbox": name, "address": account.address})
    ops.request_sync(session, account.id)
    return _account_out(session, ctx.admin, account, accounts.access_of(session, account.id, ctx.admin.id))


@router.put("/accounts/{account_id}", response_model=AccountOut)
def change_account(account_id: int, data: accounts.AccountIn, ctx: CurrentAdmin, session: AdminDb) -> AccountOut:
    account, access = accounts.require(session, ctx.admin, account_id, manage=True)
    before = {k: getattr(account, k) for k in ("address", "display_name", "imap_host", "smtp_host", "login", "sync_days")}
    if not account.team_mailbox and account.password_enc:
        imap.forget(accounts.imap_server(session, account))
    accounts.update(session, account, data)
    record(session, ctx.admin, "mail.account_changed", target_type="mail_account", target_id=account.id, ip=ctx.ip,
           metadata={"before": before, "password_changed": bool(data.password)})
    ops.request_sync(session, account.id)
    return _account_out(session, ctx.admin, account, access)


@router.delete("/accounts/{account_id}", status_code=204)
def remove_account(account_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    """Remove the account from Mail: its mirror, drafts and outbox go; the mailbox on the server stays as it is."""
    account, _ = accounts.require(session, ctx.admin, account_id, manage=True)
    record(session, ctx.admin, "mail.account_removed", target_type="mail_account", target_id=account.id, ip=ctx.ip, metadata={"address": account.address})
    for upload in session.execute(select(MailUpload).join(MailDraft, MailUpload.draft_id == MailDraft.id).where(MailDraft.account_id == account.id)).scalars():
        session.delete(upload)
    for upload in session.execute(select(MailUpload).join(MailOutbox, MailUpload.outbox_id == MailOutbox.id).where(MailOutbox.account_id == account.id)).scalars():
        session.delete(upload)
    session.delete(account)
    return Response(status_code=204)


@router.post("/accounts/{account_id}/sync", status_code=202)
def sync_now(account_id: int, ctx: CurrentAdmin, session: AdminDb, folder_id: int | None = Query(default=None)) -> dict[str, str]:
    accounts.require(session, ctx.admin, account_id)
    ops.request_sync(session, account_id, folder_id)
    return {"status": "queued"}


@router.put("/accounts/{account_id}/signature", response_model=AccountOut)
def set_signature(account_id: int, data: SignatureIn, ctx: CurrentAdmin, session: AdminDb) -> AccountOut:
    account, access = accounts.member(session, ctx.admin, account_id)
    access.signature_html = parse.clean_for_sending(data.signature_html)
    return _account_out(session, ctx.admin, account, access)


@router.get("/accounts/{account_id}/access", response_model=list[AccessOut])
def get_access(account_id: int, ctx: CurrentAdmin, session: AdminDb) -> list[AccessOut]:
    accounts.require(session, ctx.admin, account_id, manage=True)
    rows = session.execute(select(MailAccess, AdminUser).join(AdminUser, AdminUser.id == MailAccess.admin_id).where(MailAccess.account_id == account_id)).all()
    return [AccessOut(admin_id=x.admin_id, can_send=x.can_send, can_manage=x.can_manage, name=u.full_name, email=u.email) for x, u in rows]


@router.put("/accounts/{account_id}/access", response_model=list[AccessOut])
def set_access(account_id: int, entries: list[AccessEntry], ctx: CurrentAdmin, session: AdminDb) -> list[AccessOut]:
    """Who on the team may open the account, send from it and manage it."""
    accounts.require(session, ctx.admin, account_id, manage=True)
    wanted = {e.admin_id: e for e in entries}
    people = {u.id: u for u in session.execute(select(AdminUser).where(AdminUser.id.in_(list(wanted)))).scalars()} if wanted else {}
    if set(wanted) - set(people):
        raise ValidationFailed("Someone in the list is not on the team.", code="MAIL_ACCESS_UNKNOWN")
    if not any(e.can_manage for e in entries) and ctx.admin.role != "owner":
        raise ValidationFailed("Keep at least one person who manages the account.", code="MAIL_ACCESS_NO_MANAGER")
    current = {x.admin_id: x for x in session.execute(select(MailAccess).where(MailAccess.account_id == account_id)).scalars()}
    now = datetime.now(UTC)
    for admin_id, gone in current.items():
        if admin_id not in wanted:
            session.delete(gone)
    for admin_id, entry in wanted.items():
        row = current.get(admin_id)
        if row is None:
            session.add(MailAccess(account_id=account_id, admin_id=admin_id, can_send=entry.can_send, can_manage=entry.can_manage, created_at=now))
        else:
            row.can_send, row.can_manage = entry.can_send, entry.can_manage
    session.flush()
    record(session, ctx.admin, "mail.access_changed", target_type="mail_account", target_id=account_id, ip=ctx.ip,
           metadata={"access": [{"admin_id": e.admin_id, "send": e.can_send, "manage": e.can_manage} for e in entries]})
    return get_access(account_id, ctx, session)


@router.get("/people", response_model=list[PersonOut])
def people(ctx: CurrentAdmin, session: AdminDb) -> list[PersonOut]:
    """The team, to give access to an account."""
    return [PersonOut(id=u.id, name=u.full_name, email=u.email)
            for u in session.execute(select(AdminUser).where(AdminUser.status == "active").order_by(AdminUser.full_name, AdminUser.email)).scalars()]


# --- folders -----------------------------------------------------------------------------------------------
@router.post("/accounts/{account_id}/folders", response_model=FolderOut, status_code=201)
def add_folder(account_id: int, data: FolderIn, ctx: CurrentAdmin, session: AdminDb) -> FolderOut:
    return _folder_out(ops.create_folder(session, ctx.admin, account_id, data.name, data.parent_id))


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def rename_folder(folder_id: int, data: FolderRenameIn, ctx: CurrentAdmin, session: AdminDb) -> FolderOut:
    return _folder_out(ops.rename_folder(session, ctx.admin, folder_id, data.name))


@router.delete("/folders/{folder_id}", status_code=204)
def remove_folder(folder_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    ops.delete_folder(session, ctx.admin, folder_id)
    return Response(status_code=204)


@router.post("/folders/{folder_id}/empty")
def empty_folder(folder_id: int, ctx: CurrentAdmin, session: AdminDb) -> dict[str, int]:
    return {"removed": ops.empty_folder(session, ctx.admin, folder_id)}


@router.post("/folders/{folder_id}/older")
def older(folder_id: int, ctx: CurrentAdmin, session: AdminDb) -> dict[str, int]:
    """Fetch mail older than the account's sync days from the server (scrolled to the end)."""
    folder = session.get(MailFolder, folder_id)
    if folder is None:
        raise NotFound("No such folder.", code="MAIL_FOLDER_NOT_FOUND")
    account, _ = accounts.require(session, ctx.admin, folder.account_id)
    from ..services.webmail import sync

    with imap.connection(accounts.imap_server(session, account)) as conn:
        stored = sync.load_older(session, account, folder, conn, sync.own_addresses(session, account))
    return {"stored": stored}


# --- messages ----------------------------------------------------------------------------------------------
_OPERATOR = re.compile(r'(\w+):("[^"]*"|\S+)')


@router.get("/messages", response_model=MessagePage)
def messages(ctx: CurrentAdmin, session: AdminDb, folder_id: int | None = Query(default=None), view: str = Query(default="", max_length=20),
             account_id: int | None = Query(default=None), q: str = Query(default="", max_length=300), filter: str = Query(default="", max_length=20),  # noqa: A002 - the query parameter's name
             before: str = Query(default="", max_length=60), limit: int = Query(default=PAGE, ge=1, le=200)) -> MessagePage:
    """A folder (folder_id), or across accounts: view=inbox (all inboxes), flagged, unread; q searches (also from:, to:, subject:,
    has:attachment, is:unread, is:flagged)."""
    ids = accounts.visible_ids(session, ctx.admin)
    if account_id is not None:
        if account_id not in ids:
            raise NotFound("No such mail account.", code="MAIL_ACCOUNT_NOT_FOUND")
        ids = [account_id]
    stmt = select(MailMessage).where(MailMessage.account_id.in_(ids))
    has_older = False
    if folder_id is not None:
        folder = session.get(MailFolder, folder_id)
        if folder is None or folder.account_id not in ids:
            raise NotFound("No such folder.", code="MAIL_FOLDER_NOT_FOUND")
        stmt = stmt.where(MailMessage.folder_id == folder_id)
        has_older = folder.has_older
    elif view in ("inbox", "sent", "drafts", "archive", "junk", "trash"):
        stmt = stmt.join(MailFolder, MailFolder.id == MailMessage.folder_id).where(MailFolder.role == view)
    elif view == "flagged":
        stmt = stmt.where(MailMessage.flagged.is_(True))
    elif view == "unread":
        stmt = stmt.join(MailFolder, MailFolder.id == MailMessage.folder_id).where(MailFolder.role == "inbox", MailMessage.seen.is_(False))
    elif not q:
        raise ValidationFailed("Choose a folder or a view.", code="MAIL_NO_FOLDER")
    if q:
        rest = q
        for key, value in _OPERATOR.findall(q):
            value_clean = value.strip('"')
            key_lower = key.lower()
            like = f"%{value_clean}%"
            if key_lower == "from":
                stmt = stmt.where(or_(MailMessage.from_address.ilike(like), MailMessage.from_name.ilike(like)))
            elif key_lower == "to":
                stmt = stmt.where(or_(cast(MailMessage.to, String).ilike(like), cast(MailMessage.cc, String).ilike(like)))
            elif key_lower == "subject":
                stmt = stmt.where(MailMessage.subject.ilike(like))
            elif key_lower == "has" and value_clean.lower().startswith("attach"):
                stmt = stmt.where(MailMessage.has_attachments.is_(True))
            elif key_lower == "is" and value_clean.lower() == "unread":
                stmt = stmt.where(MailMessage.seen.is_(False))
            elif key_lower == "is" and value_clean.lower() in ("flagged", "starred"):
                stmt = stmt.where(MailMessage.flagged.is_(True))
            else:
                continue
            rest = rest.replace(f"{key}:{value}", " ")
        for term in rest.split()[:8]:
            like = f"%{term}%"
            stmt = stmt.where(or_(MailMessage.subject.ilike(like), MailMessage.from_address.ilike(like), MailMessage.from_name.ilike(like),
                                  cast(MailMessage.to, String).ilike(like), MailMessage.text.ilike(like)))
    if filter == "unread":
        stmt = stmt.where(MailMessage.seen.is_(False))
    elif filter == "flagged":
        stmt = stmt.where(MailMessage.flagged.is_(True))
    elif filter == "attachments":
        stmt = stmt.where(MailMessage.has_attachments.is_(True))
    if before:
        try:
            stamp, last_id = before.rsplit("|", 1)
            moment = datetime.fromisoformat(stamp)
            stmt = stmt.where(or_(MailMessage.received_at < moment, and_(MailMessage.received_at == moment, MailMessage.id < int(last_id))))
        except ValueError as exc:
            raise ValidationFailed("Bad page marker.", code="MAIL_BAD_CURSOR") from exc
    rows = list(session.execute(stmt.order_by(MailMessage.received_at.desc(), MailMessage.id.desc()).limit(limit + 1)).scalars())
    more = len(rows) > limit
    rows = rows[:limit]
    return MessagePage(items=[MessageSummary(**_summary(m)) for m in rows],
                       next=f"{rows[-1].received_at.isoformat()}|{rows[-1].id}" if more and rows else None, has_older_on_server=has_older and not more)


@router.get("/messages/{message_id}", response_model=MessageDetail)
def message(message_id: int, ctx: CurrentAdmin, session: AdminDb) -> MessageDetail:
    row, access = _message(session, ctx.admin, message_id)
    folder = session.get(MailFolder, row.folder_id)
    folders = {f.id: f for f in session.execute(select(MailFolder).where(MailFolder.account_id == row.account_id)).scalars()}
    html = _with_cids(row, row.html)
    thread: list[ThreadItem] = []
    if row.thread_key:
        for other in session.execute(select(MailMessage).where(MailMessage.account_id == row.account_id, MailMessage.thread_key == row.thread_key, MailMessage.id != row.id)
                                     .order_by(MailMessage.received_at).limit(30)).scalars():
            if other.message_id and other.message_id == row.message_id:
                continue
            f = folders.get(other.folder_id)
            thread.append(ThreadItem(**_summary(other), folder=_folder_out(f).name if f else ""))
    attachments = [AttachmentOut(index=int(a["index"]), filename=str(a["filename"]), mime=str(a["mime"]), size=int(a["size"]), inline=bool(a.get("inline")),
                                 url=f"/admin-api/mail/messages/{row.id}/attachments/{a['index']}") for a in row.attachments]
    return MessageDetail(**_summary(row), folder=_folder_out(folder).name if folder else "", folder_role=folder.role if folder else "", cc=row.cc, bcc=row.bcc,
                         reply_to=row.reply_to, sent_at=row.sent_at, html=html, text=row.text, body_state=row.body_state, attachments=attachments,
                         remote_images=bool(_REMOTE.search(row.html)), list_unsubscribe=row.list_unsubscribe, thread=thread,
                         can_send=access.can_send)


_FRAME_STYLE = (
    "body{margin:0;padding:2px 2px 12px;font:14px/1.55 Arial,Helvetica,sans-serif;color:%s;background:transparent;overflow-wrap:anywhere}"
    "img{max-width:100%%;height:auto}table{max-width:100%%}pre{white-space:pre-wrap}"
    "blockquote{margin:0 0 0 .8ex;border-left:2px solid #c7d2fe;padding-left:1ex}a{color:%s}"
)


@router.get("/messages/{message_id}/frame")
def frame(message_id: int, ctx: CurrentAdmin, session: AdminDb, images: bool = Query(default=False), dark: bool = Query(default=False)) -> Response:
    """The mail's text as a page of its own for the reading pane's frame: no scripts, no forms, framed only by the
    admin, and pictures from the internet only when the reader asked for them (they tell the sender a mail was opened)."""
    row, _ = _message(session, ctx.admin, message_id)
    body = _with_cids(row, row.html) if row.html else parse.text_to_html(row.text)
    remote = " https: http:" if images else ""
    policy = (f"default-src 'none'; img-src 'self' data:{remote}; style-src 'unsafe-inline'; font-src data:{' https:' if images else ''}; "
              "media-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox")
    style = _FRAME_STYLE % (("#e4e4e7", "#5eead4") if dark else ("#1f2937", "#0f6b5c"))
    page = f'<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>{style}</style></head><body>{body}</body></html>'
    return Response(content=page, media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": policy, "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


@router.post("/messages/actions")
def act(data: ActionIn, ctx: CurrentAdmin, session: AdminDb) -> dict[str, Any]:
    return ops.act(session, ctx.admin, data.ids, data.action, folder_id=data.folder_id)


@router.get("/messages/{message_id}/attachments/{index}")
def attachment(message_id: int, index: int, ctx: CurrentAdmin, session: AdminDb, inline: bool = Query(default=False)) -> Response:
    row, _ = _message(session, ctx.admin, message_id)
    known = next((a for a in row.attachments if int(a["index"]) == index), None)
    if known is None:
        raise NotFound("No such file in this message.", code="MAIL_ATTACHMENT_NOT_FOUND")
    part = parse.part_of(ops.raw_message(session, ctx.admin, row), index)
    if part is None:
        raise NotFound("No such file in this message.", code="MAIL_ATTACHMENT_NOT_FOUND")
    filename, mime, data = part
    outgoing.scan(data)
    show = inline and mime in ("image/png", "image/jpeg", "image/gif", "image/webp")
    return _download(data, filename, mime, inline=show)


@router.get("/messages/{message_id}/source")
def source(message_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    """The message as the server has it (.eml), to keep or to show someone."""
    row, _ = _message(session, ctx.admin, message_id)
    raw = ops.raw_message(session, ctx.admin, row)
    name = re.sub(r"[^\w\- ]", "_", parse.normalise_subject(row.subject))[:80] or "message"
    return _download(raw, f"{name}.eml", "message/rfc822")


# --- drafts ------------------------------------------------------------------------------------------------
@router.get("/drafts", response_model=list[DraftSummary])
def drafts(ctx: CurrentAdmin, session: AdminDb) -> list[DraftSummary]:
    rows = session.execute(select(MailDraft).where(MailDraft.admin_id == ctx.admin.id).order_by(MailDraft.updated_at.desc()).limit(200)).scalars()
    return [DraftSummary(id=d.id, account_id=d.account_id, to=d.to, subject=d.subject, preview=parse.html_to_text(d.html)[:160], updated_at=d.updated_at) for d in rows]


@router.post("/drafts", response_model=DraftOut, status_code=201)
def start_draft(data: DraftStartIn, ctx: CurrentAdmin, session: AdminDb) -> DraftOut:
    source_row = None
    account_id = data.account_id
    if data.message_id:
        source_row, _ = _message(session, ctx.admin, data.message_id)
        account_id = source_row.account_id
    if account_id is None:
        senders = [a for a, x in accounts.visible(session, ctx.admin) if x.can_send]
        if not senders:
            raise ValidationFailed("Add a mail account first (or ask for access to one).", code="MAIL_NO_ACCOUNT")
        account_id = senders[0].id
    account, access = accounts.member(session, ctx.admin, account_id, send=True)
    draft = outgoing.start(session, ctx.admin, account, access, data.mode, source_row)
    if data.to and data.mode == "new":
        draft.to = outgoing.recipients(data.to)
    if source_row is not None and data.mode == "forward":
        outgoing.copy_attachments(session, ctx.admin, draft, source_row)
    return _draft_out(session, draft)


@router.post("/messages/{message_id}/edit-draft", response_model=DraftOut)
def edit_server_draft(message_id: int, ctx: CurrentAdmin, session: AdminDb) -> DraftOut:
    row, access = _message(session, ctx.admin, message_id)
    if not access.can_send:
        raise ValidationFailed("You may read this account but not send from it.", code="MAIL_READ_ONLY")
    return _draft_out(session, outgoing.from_server_draft(session, ctx.admin, row))


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, ctx: CurrentAdmin, session: AdminDb) -> DraftOut:
    return _draft_out(session, outgoing.get_draft(session, ctx.admin, draft_id))


@router.put("/drafts/{draft_id}", response_model=DraftOut)
def save_draft(draft_id: int, data: DraftIn, ctx: CurrentAdmin, session: AdminDb, server: bool = Query(default=False)) -> DraftOut:
    """Saved as someone writes (every few seconds); server=true also keeps the copy in the Drafts folder (on closing)."""
    draft = outgoing.get_draft(session, ctx.admin, draft_id)
    if data.account_id and data.account_id != draft.account_id:
        accounts.require(session, ctx.admin, data.account_id, send=True)
        if draft.server_uid:
            outgoing.discard_server_copy(session, draft)
        draft.account_id = data.account_id
    draft.to, draft.cc, draft.bcc = [v.strip() for v in data.to if v.strip()], [v.strip() for v in data.cc if v.strip()], [v.strip() for v in data.bcc if v.strip()]
    draft.subject, draft.html, draft.updated_at = data.subject, data.html, datetime.now(UTC)
    if server:
        try:
            outgoing.save_to_server(session, ctx.admin, draft)
        except (imap.MailServerError, ValidationFailed):
            pass  # kept here; the server copy follows next time
    return _draft_out(session, draft)


@router.delete("/drafts/{draft_id}", status_code=204)
def discard_draft(draft_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    outgoing.discard(session, ctx.admin, outgoing.get_draft(session, ctx.admin, draft_id))
    return Response(status_code=204)


@router.post("/drafts/{draft_id}/files", response_model=UploadOut, status_code=201)
def attach(draft_id: int, ctx: CurrentAdmin, session: AdminDb, file: UploadFile = File(...), inline: bool = Query(default=False)) -> UploadOut:  # noqa: B008 - FastAPI's form parameter
    draft = outgoing.get_draft(session, ctx.admin, draft_id)
    data = file.file.read(outgoing.MAX_FILE + 1)
    return _upload_out(outgoing.add_upload(session, ctx.admin, draft, file.filename or "file", data, file.content_type or "", inline=inline))


@router.delete("/drafts/{draft_id}/files/{upload_id}", status_code=204)
def detach(draft_id: int, upload_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    outgoing.get_draft(session, ctx.admin, draft_id)
    upload = session.get(MailUpload, upload_id)
    if upload is None or upload.draft_id != draft_id:
        raise NotFound("No such file.", code="MAIL_UPLOAD_NOT_FOUND")
    session.delete(upload)
    return Response(status_code=204)


@router.get("/uploads/{upload_id}")
def upload_file(upload_id: int, ctx: CurrentAdmin, session: AdminDb) -> Response:
    upload = session.get(MailUpload, upload_id)
    if upload is None or upload.admin_id != ctx.admin.id:
        raise NotFound("No such file.", code="MAIL_UPLOAD_NOT_FOUND")
    image = upload.mime_type in ("image/png", "image/jpeg", "image/gif", "image/webp")
    return _download(upload.data, upload.filename, upload.mime_type if image else "application/octet-stream", inline=image)


@router.post("/drafts/{draft_id}/send", response_model=OutboxOut)
def send(draft_id: int, data: SendIn, ctx: CurrentAdmin, session: AdminDb) -> OutboxOut:
    draft = outgoing.get_draft(session, ctx.admin, draft_id)
    return _outbox_out(outgoing.queue(session, ctx.admin, draft, undo_seconds=data.undo_seconds, send_at=data.send_at))


# --- outbox ------------------------------------------------------------------------------------------------
@router.get("/outbox", response_model=list[OutboxOut])
def outbox(ctx: CurrentAdmin, session: AdminDb) -> list[OutboxOut]:
    rows = session.execute(select(MailOutbox).where(MailOutbox.admin_id == ctx.admin.id, MailOutbox.status.in_(("queued", "sending", "failed")))
                           .order_by(MailOutbox.send_at)).scalars()
    return [_outbox_out(o) for o in rows]


@router.get("/outbox/{outbox_id}", response_model=OutboxOut)
def outbox_item(outbox_id: int, ctx: CurrentAdmin, session: AdminDb) -> OutboxOut:
    row = session.get(MailOutbox, outbox_id)
    if row is None or row.admin_id != ctx.admin.id:
        raise NotFound("No such mail in the outbox.", code="MAIL_OUTBOX_NOT_FOUND")
    return _outbox_out(row)


@router.post("/outbox/{outbox_id}/cancel", response_model=DraftOut)
def cancel(outbox_id: int, ctx: CurrentAdmin, session: AdminDb) -> DraftOut:
    """Undo (or stop a scheduled or failed mail): it comes back as a draft."""
    row = session.get(MailOutbox, outbox_id)
    if row is None or row.admin_id != ctx.admin.id:
        raise NotFound("No such mail in the outbox.", code="MAIL_OUTBOX_NOT_FOUND")
    accounts.require(session, ctx.admin, row.account_id)
    return _draft_out(session, outgoing.cancel(session, ctx.admin, row))


@router.post("/outbox/{outbox_id}/retry", response_model=OutboxOut)
def retry(outbox_id: int, ctx: CurrentAdmin, session: AdminDb) -> OutboxOut:
    row = session.get(MailOutbox, outbox_id)
    if row is None or row.admin_id != ctx.admin.id:
        raise NotFound("No such mail in the outbox.", code="MAIL_OUTBOX_NOT_FOUND")
    if row.status != "failed":
        raise ValidationFailed("Only a mail that failed is sent again.", code="MAIL_NOT_FAILED")
    accounts.require(session, ctx.admin, row.account_id, send=True)
    from ..workers import queue as q

    row.status, row.error, row.send_at = "queued", "", datetime.now(UTC)
    q.enqueue(session, "mail.send", {"outbox_id": row.id}, queue="notifications", priority=1, max_attempts=1, dedupe_key=f"mail.send:{row.id}")
    return _outbox_out(row)


# --- addresses ---------------------------------------------------------------------------------------------
@router.get("/contacts", response_model=list[ContactOut])
def contacts(ctx: CurrentAdmin, session: AdminDb, q: str = Query(min_length=1, max_length=100)) -> list[ContactOut]:
    """Addresses to suggest while typing: people written with, and the contacts of companies on our list."""
    ids = accounts.visible_ids(session, ctx.admin)
    like = f"%{q.strip()}%"
    found: dict[str, ContactOut] = {}
    if ids:
        rows = session.execute(select(MailMessage.from_name, MailMessage.from_address, func.count()).where(
            MailMessage.account_id.in_(ids), or_(MailMessage.from_address.ilike(like), MailMessage.from_name.ilike(like)))
            .group_by(MailMessage.from_name, MailMessage.from_address).order_by(func.count().desc()).limit(20)).all()
        for name, address, _n in rows:
            found.setdefault(address.lower(), ContactOut(name=name, address=address.lower()))
        sent = session.execute(select(MailMessage.to, MailMessage.cc).join(MailFolder, MailFolder.id == MailMessage.folder_id).where(
            MailMessage.account_id.in_(ids), MailFolder.role == "sent", or_(cast(MailMessage.to, String).ilike(like), cast(MailMessage.cc, String).ilike(like)))
            .order_by(MailMessage.received_at.desc()).limit(200)).all()
        needle = q.strip().lower()
        for to, cc in sent:
            for name, address in [*(to or []), *(cc or [])]:
                if needle in f"{name} {address}".lower():
                    found.setdefault(address.lower(), ContactOut(name=name, address=address.lower()))
    own = {a.address.lower() for a, _ in accounts.visible(session, ctx.admin)}
    return [c for c in found.values() if c.address not in own][:12]
