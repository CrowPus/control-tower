"""Mirroring an account: its folders, new mail, what was read, flagged, moved or
deleted elsewhere (on the phone, in webmail). The worker runs it every minute and when someone presses
refresh; the first sync of a folder reaches back the account's "sync days", newest mail first, and
continues over the next runs so a large mailbox never holds up the others.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...core.errors import ValidationFailed
from ...core.logging import get_logger
from ...models import MailAccount, MailFolder, MailMessage
from . import accounts, imap, parse, utf7

log = get_logger(__name__)

# Safety rails for one run, not business values: how many messages a folder takes in at once, the
# largest message read whole (bigger ones are read without their attachments' content), and how long
# one account may take before the rest waits for the next run.
PER_RUN = 300
BATCH = 25
MAX_FULL = 25 * 1024 * 1024
RUN_SECONDS = 50.0
FLAG_CHECK = timedelta(minutes=5)

ROLE_FLAGS = {"\\Sent": "sent", "\\Drafts": "drafts", "\\Trash": "trash", "\\Junk": "junk", "\\Archive": "archive", "\\All": "", "\\Flagged": ""}
ROLE_NAMES = {
    "sent": ("sent", "gesendet", "gesendete objekte", "gesendete elemente", "sent items", "sent messages", "sent mail"),
    "drafts": ("drafts", "entwürfe", "entwurf", "draft"),
    "trash": ("trash", "papierkorb", "gelöschte objekte", "gelöschte elemente", "deleted items", "deleted messages", "bin"),
    "junk": ("junk", "spam", "junk-e-mail", "junk e-mail", "spamverdacht", "bulk mail"),
    "archive": ("archive", "archiv", "archives"),
}
ORDER = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "junk": 4, "trash": 5, "": 6}


def role_of(folder: imap.Folder) -> str:
    if folder.name.upper() == "INBOX":
        return "inbox"
    for flag in folder.flags:
        if flag in ROLE_FLAGS and ROLE_FLAGS[flag]:
            return ROLE_FLAGS[flag]
    leaf = utf7.decode(folder.name).split(folder.delimiter)[-1].strip().lower() if folder.delimiter else utf7.decode(folder.name).strip().lower()
    for role, names in ROLE_NAMES.items():
        if leaf in names:
            return role
    return ""


def sync_folders(session: Session, account: MailAccount, conn: imap.Connection) -> list[MailFolder]:
    remote = [f for f in conn.folders() if not {"\\NOSELECT", "\\NONEXISTENT"} & {x.upper() for x in f.flags}]
    local = {f.name: f for f in session.execute(select(MailFolder).where(MailFolder.account_id == account.id)).scalars()}
    seen_roles: set[str] = set()
    names = set()
    for folder in remote:
        names.add(folder.name)
        role = role_of(folder)
        # One folder per role: the server's marked one wins, else the first with a usual name.
        if role and role in seen_roles:
            role = ""
        if role:
            seen_roles.add(role)
        row = local.get(folder.name)
        if row is None:
            row = MailFolder(account_id=account.id, name=folder.name, display_name=utf7.decode(folder.name), delimiter=folder.delimiter or "/", role=role)
            session.add(row)
            local[folder.name] = row
        else:
            row.role, row.delimiter, row.display_name = role, folder.delimiter or "/", utf7.decode(folder.name)
    for name, row in list(local.items()):
        if name not in names:
            session.delete(row)
            del local[name]
    session.flush()
    return sorted(local.values(), key=lambda f: (ORDER.get(f.role, 6), f.display_name.lower()))


def _thread_key(session: Session, account_id: int, parsed: parse.Parsed, fallback: str) -> str:
    refs = parsed.references.split()
    if refs:
        return refs[0][:300]
    if parsed.in_reply_to:
        parent = session.execute(select(MailMessage.thread_key).where(MailMessage.account_id == account_id, MailMessage.message_id == parsed.in_reply_to).limit(1)).scalar()
        return (parent or parsed.in_reply_to)[:300]
    return (parsed.message_id or fallback)[:300]


def _apply_flags(row: MailMessage, flags: set[str]) -> None:
    upper = {f.upper() for f in flags}
    row.seen = "\\SEEN" in upper
    row.flagged = "\\FLAGGED" in upper
    row.answered = "\\ANSWERED" in upper
    row.forwarded = "$FORWARDED" in upper
    row.is_draft = "\\DRAFT" in upper


def store(session: Session, account: MailAccount, folder: MailFolder, fetched: imap.Fetched, own: set[str]) -> MailMessage | None:
    if session.execute(select(MailMessage.id).where(MailMessage.folder_id == folder.id, MailMessage.uid == fetched.uid)).first():
        return None
    raw = fetched.body or b""
    parsed = parse.parse(raw) if raw else parse.Parsed()
    now = datetime.now(UTC)
    row = MailMessage(account_id=account.id, folder_id=folder.id, uid=fetched.uid, message_id=parsed.message_id, in_reply_to=parsed.in_reply_to,
                      references=parsed.references, subject=parsed.subject, from_name=parsed.from_name, from_address=parsed.from_address.lower(),
                      to=parsed.to, cc=parsed.cc, bcc=parsed.bcc, reply_to=parsed.reply_to, sent_at=parsed.sent_at,
                      received_at=fetched.internal_date or parsed.sent_at or now, size=fetched.size or len(raw),
                      has_attachments=any(not a["inline"] for a in parsed.attachments), attachments=parsed.attachments, preview=parsed.preview,
                      text=parsed.text, html=parsed.html, body_state="full" if fetched.size <= MAX_FULL else "too_large",
                      list_unsubscribe=parsed.list_unsubscribe, draft_ref=parsed.draft_ref, created_at=now)
    row.thread_key = _thread_key(session, account.id, parsed, f"{folder.id}:{fetched.uid}")
    _apply_flags(row, fetched.flags)
    session.add(row)
    return row


def _fetch_into(session: Session, account: MailAccount, folder: MailFolder, conn: imap.Connection, uids: list[int], own: set[str]) -> int:
    stored = 0
    for start in range(0, len(uids), BATCH):
        chunk = uids[start:start + BATCH]
        sizes = {f.uid: f.size for f in conn.fetch(chunk, "(UID RFC822.SIZE)")}
        small = [u for u in chunk if sizes.get(u, 0) <= MAX_FULL and u in sizes]
        large = [u for u in chunk if sizes.get(u, 0) > MAX_FULL]
        rows = conn.fetch(small, "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[])") if small else []
        if large:
            rows += conn.fetch(large, "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER])")
        for fetched in rows:
            if store(session, account, folder, fetched, own):
                stored += 1
        session.flush()
    return stored


def _mirrored(session: Session, folder: MailFolder) -> set[int]:
    return set(session.execute(select(MailMessage.uid).where(MailMessage.folder_id == folder.id)).scalars())


def _bounds(session: Session, folder: MailFolder, server_total: int) -> None:
    lo, hi, count = session.execute(select(func.min(MailMessage.uid), func.max(MailMessage.uid), func.count()).where(MailMessage.folder_id == folder.id)).one()
    folder.first_uid = int(lo or 0)
    folder.last_uid = max(folder.last_uid, int(hi or 0))
    # Mail on the server that is not mirrored (older than the sync days, or not yet fetched).
    folder.has_older = server_total > int(count or 0)


def sync_folder(session: Session, account: MailAccount, folder: MailFolder, conn: imap.Connection, own: set[str], *, force: bool = False) -> dict[str, int]:
    status = conn.status(folder.name)
    validity = status.get("UIDVALIDITY", 0)
    if folder.uid_validity and validity and validity != folder.uid_validity:
        # The server renumbered the folder: everything in it is read again.
        session.execute(delete(MailMessage).where(MailMessage.folder_id == folder.id))
        folder.last_uid = folder.first_uid = 0
        folder.window_done = False
    folder.uid_validity = validity or folder.uid_validity
    uidnext = status.get("UIDNEXT", 0)
    total = status.get("MESSAGES", 0)
    now = datetime.now(UTC)
    first = folder.synced_at is None or folder.last_uid == 0 and not folder.window_done
    new_needed = not uidnext or uidnext - 1 > folder.last_uid
    changed = total != folder.total or status.get("UNSEEN", -1) != folder.unread
    due = folder.synced_at is None or now - folder.synced_at > FLAG_CHECK
    if not (force or first or new_needed or changed or due or not folder.window_done):
        return {"new": 0}
    conn.select(folder.name, readonly=True)
    since = (now - timedelta(days=account.sync_days)).strftime("%d-%b-%Y")
    stored = 0
    if first:
        found = conn.search("SINCE", since)
        take = found[-PER_RUN:]
        # Newest first, so the latest mail is there at once.
        stored += _fetch_into(session, account, folder, conn, list(reversed(take)), own)
        folder.last_uid = max(uidnext - 1, max(found) if found else 0, 0)
        folder.window_done = len(take) == len(found)
    else:
        if new_needed:
            found = [u for u in conn.search("UID", f"{folder.last_uid + 1}:*") if u > folder.last_uid][:PER_RUN]
            stored += _fetch_into(session, account, folder, conn, found, own)
            if found:
                folder.last_uid = max(found)
        if not folder.window_done and stored < PER_RUN:
            # The rest of the sync days the first run left for later.
            missing = [u for u in conn.search("SINCE", since) if u not in _mirrored(session, folder)]
            take = missing[-(PER_RUN - stored):]
            stored += _fetch_into(session, account, folder, conn, list(reversed(take)), own)
            folder.window_done = len(take) == len(missing)
    mirrored = _mirrored(session, folder)
    if mirrored:
        # What changed elsewhere: flags, and messages gone from the server (moved or deleted).
        remote = {f.uid: f.flags for f in conn.fetch_range(min(mirrored), "(UID FLAGS)")} if total else {}
        # An empty answer for a folder the server says is not empty is a hiccup, never "everything was deleted".
        if remote or total == 0:
            for row in session.execute(select(MailMessage).where(MailMessage.folder_id == folder.id)).scalars().all():
                if row.uid not in remote:
                    session.delete(row)
                else:
                    _apply_flags(row, remote[row.uid])
            session.flush()
    _bounds(session, folder, total)
    folder.total = total
    folder.unread = status.get("UNSEEN", folder.unread)
    folder.synced_at = now
    session.flush()
    return {"new": stored}


def load_older(session: Session, account: MailAccount, folder: MailFolder, conn: imap.Connection, own: set[str], count: int = 100) -> int:
    """Mail older than the account's sync days, when someone scrolls to the end and asks for it."""
    conn.select(folder.name, readonly=True)
    mirrored = _mirrored(session, folder)
    oldest = session.execute(select(func.min(MailMessage.received_at)).where(MailMessage.folder_id == folder.id)).scalar()
    candidates = [u for u in conn.search("BEFORE", (oldest + timedelta(days=1)).strftime("%d-%b-%Y")) if u not in mirrored] if oldest else []
    if not candidates:
        candidates = [u for u in conn.search("ALL") if u not in mirrored]
    take = candidates[-count:]
    stored = _fetch_into(session, account, folder, conn, list(reversed(take)), own)
    _bounds(session, folder, conn.status(folder.name).get("MESSAGES", folder.total))
    return stored


def own_addresses(session: Session, account: MailAccount) -> set[str]:
    return {accounts.sender(session, account)[1].lower(), account.address.lower()}


def sync_account(session: Session, account: MailAccount, *, folder_id: int | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        server = accounts.imap_server(session, account)
        with imap.connection(server) as conn:
            folders = sync_folders(session, account, conn)
            own = own_addresses(session, account)
            new = 0
            for folder in folders:
                if folder_id is not None and folder.id != folder_id and time.monotonic() - started > 5:
                    continue
                if time.monotonic() - started > RUN_SECONDS:
                    break
                new += sync_folder(session, account, folder, conn, own, force=folder.id == folder_id)["new"]
    except imap.LoginRefused as exc:
        account.status, account.last_error = "error", exc.message[:300]
        return {"error": "login"}
    except (imap.MailServerError, ValidationFailed) as exc:
        account.status, account.last_error = "error", exc.message[:300]
        log.warning("mail sync failed for account %s: %s", account.id, exc.code)
        return {"error": exc.code}
    account.status, account.last_error, account.last_sync_at = "ok", "", datetime.now(UTC)
    return {"new": new, "folders": len(folders)}
