"""What someone does with messages and folders: read, flag, move, archive, junk,
delete, folders made, renamed and removed. Each change goes to the mail server at once, so the phone and
webmail show the same, and then to the mirror; a moved message keeps its place in the mirror when the
server says where it went (UIDPLUS), so "Undo" can move it straight back.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...core.errors import NotFound, ValidationFailed
from ...models import AdminUser, MailAccount, MailFolder, MailMessage
from ...workers import queue as q
from . import accounts, imap, utf7

Action = Literal["read", "unread", "flag", "unflag", "move", "archive", "delete", "junk", "not_junk"]
DEFAULT_NAMES = {"archive": "Archive", "trash": "Trash", "junk": "Junk"}


def folder_by_role(session: Session, account_id: int, role: str) -> MailFolder | None:
    return session.execute(select(MailFolder).where(MailFolder.account_id == account_id, MailFolder.role == role)).scalars().first()


def _ensure_role_folder(session: Session, account: MailAccount, conn: imap.Connection, role: str) -> MailFolder:
    """The account's Archive (Trash, Junk) folder, made on the server when it has none, like Outlook does."""
    existing = folder_by_role(session, account.id, role)
    if existing:
        return existing
    name = DEFAULT_NAMES[role]
    known = session.execute(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == name)).scalar_one_or_none()
    if known is None:
        conn.create(name)
        known = MailFolder(account_id=account.id, name=name, display_name=name, delimiter="/", role=role)
        session.add(known)
    known.role = role
    session.flush()
    return known


def request_sync(session: Session, account_id: int, folder_id: int | None = None) -> None:
    q.enqueue(session, "mail.sync", {"account_id": account_id, "folder_id": folder_id}, queue="maintenance", priority=2, max_attempts=1,
              dedupe_key=f"mail.sync:{account_id}")


def _refresh_counts(conn: imap.Connection, folder: MailFolder) -> None:
    try:
        status = conn.status(folder.name)
    except imap.MailServerError:
        return
    folder.total = status.get("MESSAGES", folder.total)
    folder.unread = status.get("UNSEEN", folder.unread)


def act(session: Session, admin: AdminUser, ids: list[int], action: Action, *, folder_id: int | None = None) -> dict[str, Any]:
    rows = list(session.execute(select(MailMessage).where(MailMessage.id.in_(ids))).scalars()) if ids else []
    if not rows:
        raise NotFound("No such messages.", code="MAIL_MESSAGES_NOT_FOUND")
    groups: dict[tuple[int, int], list[MailMessage]] = defaultdict(list)
    for row in rows:
        groups[(row.account_id, row.folder_id)].append(row)
    moved: dict[int, int] = {}
    deleted = 0
    for (account_id, source_id), messages in groups.items():
        account, _access = accounts.require(session, admin, account_id)
        source = session.get(MailFolder, source_id)
        if source is None:
            continue
        uids = [m.uid for m in messages]
        server = accounts.imap_server(session, account)
        with imap.connection(server) as conn:
            if action in ("read", "unread", "flag", "unflag"):
                conn.select(source.name, readonly=False)
                flag = "\\Seen" if action in ("read", "unread") else "\\Flagged"
                conn.store(uids, "+FLAGS.SILENT" if action in ("read", "flag") else "-FLAGS.SILENT", flag)
                for m in messages:
                    if flag == "\\Seen":
                        m.seen = action == "read"
                    else:
                        m.flagged = action == "flag"
                _refresh_counts(conn, source)
                continue
            target: MailFolder | None
            if action == "move":
                target = session.get(MailFolder, folder_id or 0)
                if target is None or target.account_id != account.id:
                    raise ValidationFailed("Messages move only between folders of the same account.", code="MAIL_MOVE_ACROSS")
            elif action == "archive":
                target = _ensure_role_folder(session, account, conn, "archive")
            elif action == "junk":
                target = _ensure_role_folder(session, account, conn, "junk")
            elif action == "not_junk":
                target = folder_by_role(session, account.id, "inbox")
            else:  # delete
                target = None if source.role == "trash" else _ensure_role_folder(session, account, conn, "trash")
            conn.select(source.name, readonly=False)
            if target is None:
                # Already in the trash: gone for good.
                conn.delete_messages(uids)
                for m in messages:
                    session.delete(m)
                deleted += len(messages)
                _refresh_counts(conn, source)
                continue
            if target.id == source.id:
                continue
            mapping = conn.move(uids, target.name)
            taken = set(session.execute(select(MailMessage.uid).where(MailMessage.folder_id == target.id, MailMessage.uid.in_(list(mapping.values())))).scalars()) if mapping else set()
            for m in messages:
                new_uid = mapping.get(m.uid)
                if new_uid and new_uid not in taken:
                    m.folder_id, m.uid = target.id, new_uid
                    target.last_uid = max(target.last_uid, new_uid)
                    if not target.first_uid or new_uid < target.first_uid:
                        target.first_uid = new_uid
                    moved[m.id] = target.id
                else:
                    session.delete(m)
                    deleted += 1
            _refresh_counts(conn, source)
            _refresh_counts(conn, target)
        if action not in ("read", "unread", "flag", "unflag"):
            request_sync(session, account.id)
    session.flush()
    return {"changed": len(rows), "moved": moved, "removed": deleted}


# --- folders ---------------------------------------------------------------------------------------------
def _folder(session: Session, admin: AdminUser, folder_id: int) -> tuple[MailAccount, MailFolder]:
    folder = session.get(MailFolder, folder_id)
    if folder is None:
        raise NotFound("No such folder.", code="MAIL_FOLDER_NOT_FOUND")
    account, _ = accounts.require(session, admin, folder.account_id)
    return account, folder


def _valid_name(name: str, delimiter: str) -> str:
    name = name.strip()
    if not name or len(name) > 100 or delimiter in name or any(c in name for c in "%*\r\n\"\\"):
        raise ValidationFailed("Choose a folder name without / % * \" or \\.", code="MAIL_FOLDER_NAME")
    return name


def create_folder(session: Session, admin: AdminUser, account_id: int, name: str, parent_id: int | None = None) -> MailFolder:
    account, _ = accounts.require(session, admin, account_id)
    parent = session.get(MailFolder, parent_id) if parent_id else None
    if parent is not None and parent.account_id != account.id:
        raise NotFound("No such folder.", code="MAIL_FOLDER_NOT_FOUND")
    near = parent or folder_by_role(session, account.id, "inbox")
    delimiter = near.delimiter if near is not None else "/"
    leaf = _valid_name(name, delimiter)
    full = (parent.name + delimiter if parent else "") + utf7.encode(leaf)
    if session.execute(select(MailFolder.id).where(MailFolder.account_id == account.id, MailFolder.name == full)).first():
        raise ValidationFailed("There is a folder of that name already.", code="MAIL_FOLDER_EXISTS")
    with imap.connection(accounts.imap_server(session, account)) as conn:
        conn.create(full)
    row = MailFolder(account_id=account.id, name=full, display_name=utf7.decode(full), delimiter=delimiter)
    session.add(row)
    session.flush()
    return row


def rename_folder(session: Session, admin: AdminUser, folder_id: int, name: str) -> MailFolder:
    account, folder = _folder(session, admin, folder_id)
    if folder.role == "inbox":
        raise ValidationFailed("The inbox keeps its name.", code="MAIL_FOLDER_FIXED")
    leaf = _valid_name(name, folder.delimiter)
    head = folder.name.rsplit(folder.delimiter, 1)[0] + folder.delimiter if folder.delimiter in folder.name else ""
    new = head + utf7.encode(leaf)
    with imap.connection(accounts.imap_server(session, account)) as conn:
        conn.rename(folder.name, new)
    old = folder.name
    # Folders inside it move with it.
    for child in session.execute(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name.like(old + folder.delimiter + "%"))).scalars():
        child.name = new + child.name[len(old):]
        child.display_name = utf7.decode(child.name)
    folder.name, folder.display_name = new, utf7.decode(new)
    session.flush()
    return folder


def delete_folder(session: Session, admin: AdminUser, folder_id: int) -> None:
    account, folder = _folder(session, admin, folder_id)
    if folder.role:
        raise ValidationFailed("Inbox, Sent, Drafts, Archive, Junk and Trash stay.", code="MAIL_FOLDER_FIXED")
    if session.execute(select(MailFolder.id).where(MailFolder.account_id == account.id, MailFolder.name.like(folder.name + folder.delimiter + "%"))).first():
        raise ValidationFailed("Move or delete the folders inside it first.", code="MAIL_FOLDER_NOT_EMPTY")
    with imap.connection(accounts.imap_server(session, account)) as conn:
        conn.delete_folder(folder.name)
    session.delete(folder)
    session.flush()


def empty_folder(session: Session, admin: AdminUser, folder_id: int) -> int:
    account, folder = _folder(session, admin, folder_id)
    if folder.role not in ("trash", "junk"):
        raise ValidationFailed("Only Trash and Junk can be emptied.", code="MAIL_FOLDER_NOT_EMPTIABLE")
    with imap.connection(accounts.imap_server(session, account)) as conn:
        conn.select(folder.name, readonly=False)
        uids = conn.search("ALL")
        if uids:
            conn.delete_messages(uids)
        _refresh_counts(conn, folder)
    removed = len(session.execute(select(MailMessage.id).where(MailMessage.folder_id == folder.id)).all())
    session.execute(delete(MailMessage).where(MailMessage.folder_id == folder.id))
    folder.first_uid, folder.has_older, folder.synced_at = 0, False, datetime.now(UTC)
    session.flush()
    return int(removed)


# --- the message as the server has it -----------------------------------------------------------------------
# A message read whole is kept a few minutes (its pictures and files are asked for one by one).
_RAW_TTL = 300.0
_RAW_MAX = 64 * 1024 * 1024
_raw: OrderedDict[tuple[int, int, int], tuple[float, bytes]] = OrderedDict()
_raw_lock = threading.Lock()


def _cached(key: tuple[int, int, int]) -> bytes | None:
    with _raw_lock:
        hit = _raw.get(key)
        if hit and time.monotonic() - hit[0] < _RAW_TTL:
            _raw.move_to_end(key)
            return hit[1]
        _raw.pop(key, None)
    return None


def _keep(key: tuple[int, int, int], raw: bytes) -> None:
    if len(raw) > _RAW_MAX // 4:
        return
    with _raw_lock:
        _raw[key] = (time.monotonic(), raw)
        while sum(len(v[1]) for v in _raw.values()) > _RAW_MAX:
            _raw.popitem(last=False)


def raw_message(session: Session, admin: AdminUser, message: MailMessage) -> bytes:
    account, _ = accounts.require(session, admin, message.account_id)
    key = (message.id, message.folder_id, message.uid)
    hit = _cached(key)
    if hit is not None:
        return hit
    folder = session.get(MailFolder, message.folder_id)
    if folder is None:
        raise NotFound("No such message.", code="MAIL_MESSAGE_NOT_FOUND")
    with imap.connection(accounts.imap_server(session, account)) as conn:
        conn.select(folder.name, readonly=True)
        rows = conn.fetch([message.uid], "(UID BODY.PEEK[])")
    raw = next((r.body for r in rows if r.uid == message.uid and r.body), None)
    if raw is None:
        raise NotFound("The message is no longer on the server.", code="MAIL_MESSAGE_GONE")
    _keep(key, raw)
    return raw
