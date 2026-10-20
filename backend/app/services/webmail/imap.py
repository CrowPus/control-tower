"""Talking to an account's IMAP server (RFC 3501, with MOVE, UIDPLUS and SPECIAL-USE where offered).

Connections are kept open for a short while and reused, so reading, flagging and moving a message is
quick; a connection that went quiet is checked before it is used again.
"""

from __future__ import annotations

import imaplib
import re
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ...core.errors import TransientExternalError, ValidationFailed
from .accounts import Server

TIMEOUT = 30
IDLE_CHECK = 30.0  # seconds a pooled connection may be quiet before it is checked
IDLE_DROP = 240.0  # seconds after which it is closed
POOL_SIZE = 3

# Tests and a local development server (self-signed certificate) replace this.
ssl_context: Callable[[], ssl.SSLContext] = ssl.create_default_context


class MailServerError(TransientExternalError):
    """The mail server could not be reached or refused what was asked."""


class LoginRefused(ValidationFailed):
    """The server refused the login or password."""


@dataclass
class Folder:
    name: str
    delimiter: str
    flags: set[str]


@dataclass
class Fetched:
    uid: int
    flags: set[str] = field(default_factory=set)
    internal_date: datetime | None = None
    size: int = 0
    body: bytes | None = None


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _line(raw: Any) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


_LIST = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')


def parse_list(rows: list[Any]) -> list[Folder]:
    out: list[Folder] = []
    pending: str | None = None
    for raw in rows or []:
        if isinstance(raw, tuple):
            # A name sent as a literal: ('(\\HasNoChildren) "/" {12}', b'Folder name')
            head = _line(raw[0])
            match = _LIST.match(re.sub(r"\{\d+\}$", '"__literal__"', head))
            if match:
                delim = match.group("delim")
                out.append(Folder(name=_line(raw[1]), delimiter="" if delim == "NIL" else delim.strip('"').replace("\\\\", "\\"), flags=set(match.group("flags").split())))
            continue
        if raw is None:
            continue
        line = _line(raw)
        if pending is not None:
            line, pending = pending + line, None
        match = _LIST.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        delim = match.group("delim")
        out.append(Folder(name=name, delimiter="" if delim == "NIL" else delim.strip('"').replace("\\\\", "\\"), flags=set(match.group("flags").split())))
    return out


def parse_fetch(data: list[Any]) -> list[Fetched]:
    """imaplib's FETCH answer: tuples (meta, literal) and bytes, the meta of one message spread over both."""
    records: list[tuple[str, bytes | None]] = []
    for item in data or []:
        if isinstance(item, tuple):
            records.append((_line(item[0]), item[1] if isinstance(item[1], bytes) else None))
        elif isinstance(item, bytes):
            text = _line(item)
            if re.match(r"^\d+ \(", text):
                records.append((text, None))
            elif records:
                meta, body = records[-1]
                records[-1] = (meta + " " + text, body)
    out: list[Fetched] = []
    for meta, body in records:
        uid = re.search(r"\bUID (\d+)", meta)
        if not uid:
            continue
        row = Fetched(uid=int(uid.group(1)), body=body)
        flags = re.search(r"\bFLAGS \(([^)]*)\)", meta)
        if flags:
            row.flags = set(flags.group(1).split())
        when = re.search(r'\bINTERNALDATE "([^"]+)"', meta)
        if when:
            try:
                row.internal_date = datetime.strptime(when.group(1), "%d-%b-%Y %H:%M:%S %z").astimezone(UTC)
            except ValueError:
                row.internal_date = None
        size = re.search(r"\bRFC822\.SIZE (\d+)", meta)
        if size:
            row.size = int(size.group(1))
        out.append(row)
    return out


def _uid_set(uids: list[int]) -> str:
    """1,2,3,7 → "1:3,7"."""
    ordered = sorted(set(uids))
    parts: list[str] = []
    start = prev = None
    for uid in ordered:
        if start is None:
            start = prev = uid
        elif uid == (prev or 0) + 1:
            prev = uid
        else:
            parts.append(f"{start}:{prev}" if start != prev else str(start))
            start = prev = uid
    if start is not None:
        parts.append(f"{start}:{prev}" if start != prev else str(start))
    return ",".join(parts)


class Connection:
    """One signed-in IMAP connection."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.selected: tuple[str, bool] | None = None
        self.last_used = time.monotonic()
        caps = getattr(client, "capabilities", ()) or ()
        self.capabilities = {_line(c).upper() for c in caps}

    def has(self, capability: str) -> bool:
        return capability.upper() in self.capabilities

    def _ok(self, result: tuple[str, Any], what: str) -> Any:
        status, data = result
        if status != "OK":
            detail = _line(data[0]) if data else ""
            raise MailServerError(f"The mail server refused to {what}: {detail[:150]}", code="MAIL_SERVER_REFUSED")
        return data

    def folders(self) -> list[Folder]:
        return parse_list(self._ok(self.client.list(), "list the folders"))

    def status(self, name: str) -> dict[str, int]:
        data = self._ok(self.client.status(_quote(name), "(MESSAGES UIDNEXT UIDVALIDITY UNSEEN)"), "read a folder")
        text = " ".join(_line(d) for d in data if d)
        return {key: int(value) for key, value in re.findall(r"(MESSAGES|UIDNEXT|UIDVALIDITY|UNSEEN) (\d+)", text)}

    def select(self, name: str, *, readonly: bool = True) -> int:
        if self.selected == (name, readonly):
            return -1
        data = self._ok(self.client.select(_quote(name), readonly=readonly), "open a folder")
        self.selected = (name, readonly)
        try:
            return int(_line(data[0]))
        except (ValueError, IndexError, TypeError):
            return 0

    def uid_validity(self) -> int:
        try:
            values = self.client.untagged_responses.get("UIDVALIDITY") or []
            return int(_line(values[-1])) if values else 0
        except (ValueError, AttributeError):
            return 0

    def search(self, *criteria: str) -> list[int]:
        data = self._ok(self.client.uid("SEARCH", *criteria), "search")
        return sorted(int(u) for u in " ".join(_line(d) for d in data if d).split() if u.isdigit())

    def fetch(self, uids: list[int], items: str) -> list[Fetched]:
        if not uids:
            return []
        return parse_fetch(self._ok(self.client.uid("FETCH", _uid_set(uids), items), "read messages"))

    def fetch_range(self, first_uid: int, items: str) -> list[Fetched]:
        return parse_fetch(self._ok(self.client.uid("FETCH", f"{max(first_uid, 1)}:*", items), "read messages"))

    def store(self, uids: list[int], change: str, flags: str) -> None:
        if uids:
            self._ok(self.client.uid("STORE", _uid_set(uids), change, f"({flags})"), "change flags")

    def _copyuid(self) -> dict[int, int]:
        """Old UID → new UID from the server's COPYUID answer (UIDPLUS), when it gave one."""
        values = self.client.untagged_responses.pop("COPYUID", None) or []
        mapping: dict[int, int] = {}
        for value in values:
            parts = _line(value).split()
            if len(parts) >= 3:
                old, new = _expand(parts[1]), _expand(parts[2])
                if len(old) == len(new):
                    mapping.update(zip(old, new, strict=True))
        return mapping

    def _response_code(self, data: Any, code: str) -> list[str]:
        text = " ".join(_line(d) for d in (data or []) if d)
        match = re.search(rf"\[{code} ([^\]]+)\]", text)
        return match.group(1).split() if match else []

    def move(self, uids: list[int], target: str) -> dict[int, int]:
        """Move to another folder (the source folder selected read-write); the new UIDs where known."""
        if not uids:
            return {}
        self.client.untagged_responses.pop("COPYUID", None)
        if self.has("MOVE"):
            status, data = self.client.uid("MOVE", _uid_set(uids), _quote(target))
            if status != "OK":
                raise MailServerError("The mail server refused to move the messages.", code="MAIL_SERVER_REFUSED")
            mapping = self._copyuid()
            code = self._response_code(data, "COPYUID")
            if not mapping and len(code) >= 3:
                mapping = dict(zip(_expand(code[1]), _expand(code[2]), strict=False))
            return mapping
        status, data = self.client.uid("COPY", _uid_set(uids), _quote(target))
        if status != "OK":
            raise MailServerError("The mail server refused to move the messages.", code="MAIL_SERVER_REFUSED")
        code = self._response_code(data, "COPYUID")
        mapping = dict(zip(_expand(code[1]), _expand(code[2]), strict=False)) if len(code) >= 3 else self._copyuid()
        self.store(uids, "+FLAGS.SILENT", "\\Deleted")
        self.expunge(uids)
        return mapping

    def expunge(self, uids: list[int]) -> None:
        if self.has("UIDPLUS"):
            self._ok(self.client.uid("EXPUNGE", _uid_set(uids)), "remove messages")
        else:
            self._ok(self.client.expunge(), "remove messages")

    def delete_messages(self, uids: list[int]) -> None:
        self.store(uids, "+FLAGS.SILENT", "\\Deleted")
        self.expunge(uids)

    def append(self, folder: str, flags: str, raw: bytes) -> int | None:
        status, data = self.client.append(_quote(folder), f"({flags})" if flags else None, imaplib.Time2Internaldate(time.time()), raw)
        if status != "OK":
            raise MailServerError("The mail server refused to save the message.", code="MAIL_SERVER_REFUSED")
        code = self._response_code(data, "APPENDUID")
        if len(code) >= 2 and code[1].isdigit():
            return int(code[1])
        values = self.client.untagged_responses.pop("APPENDUID", None) or []
        for value in values:
            parts = _line(value).split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return None

    def create(self, name: str) -> None:
        self._ok(self.client.create(_quote(name)), "create the folder")
        try:
            self.client.subscribe(_quote(name))
        except (imaplib.IMAP4.error, OSError):
            pass

    def rename(self, old: str, new: str) -> None:
        if self.selected and self.selected[0] == old:
            self.unselect()
        self._ok(self.client.rename(_quote(old), _quote(new)), "rename the folder")

    def delete_folder(self, name: str) -> None:
        if self.selected and self.selected[0] == name:
            self.unselect()
        self._ok(self.client.delete(_quote(name)), "delete the folder")

    def unselect(self) -> None:
        try:
            self.client.close()
        except (imaplib.IMAP4.error, OSError):
            pass
        self.selected = None

    def noop(self) -> bool:
        try:
            return bool(self.client.noop()[0] == "OK")
        except (imaplib.IMAP4.error, OSError):
            return False

    def logout(self) -> None:
        try:
            self.client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _expand(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        if ":" in part:
            a, b = part.split(":", 1)
            if a.isdigit() and b.isdigit():
                lo, hi = sorted((int(a), int(b)))
                out.extend(range(lo, hi + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def open_connection(server: Server) -> Connection:
    if not server.host or not server.password:
        raise ValidationFailed("The account's incoming server or password is missing.", code="MAIL_NOT_SET_UP")
    try:
        if server.security == "starttls":
            client: Any = imaplib.IMAP4(server.host, server.port, timeout=TIMEOUT)
            client.starttls(ssl_context=ssl_context())
        else:
            client = imaplib.IMAP4_SSL(server.host, server.port, timeout=TIMEOUT, ssl_context=ssl_context())
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise MailServerError(f"Could not reach {server.host}: {exc.__class__.__name__}", code="MAIL_SERVER_UNREACHABLE") from exc
    try:
        client.login(server.login, server.password)
    except imaplib.IMAP4.error as exc:
        try:
            client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        raise LoginRefused("The incoming server refused the login or password.", code="MAIL_LOGIN_REFUSED") from exc
    except OSError as exc:
        raise MailServerError(f"Could not sign in to {server.host}: {exc.__class__.__name__}", code="MAIL_SERVER_UNREACHABLE") from exc
    try:
        status, data = client.capability()
        if status == "OK" and data:
            client.capabilities = tuple(_line(data[0]).upper().split())
    except (imaplib.IMAP4.error, OSError):
        pass
    return Connection(client)


# --- the pool --------------------------------------------------------------------------------------------
_pool: dict[tuple[Any, ...], list[Connection]] = {}
_lock = threading.Lock()
# Tests give a fake server here: (server) → Connection.
connector: Callable[[Server], Connection] = open_connection


def _key(server: Server) -> tuple[Any, ...]:
    return (server.host, server.port, server.security, server.login, hash(server.password))


@contextmanager
def connection(server: Server) -> Iterator[Connection]:
    key = _key(server)
    conn: Connection | None = None
    with _lock:
        idle = _pool.get(key) or []
        while idle and conn is None:
            candidate = idle.pop()
            quiet = time.monotonic() - candidate.last_used
            if quiet > IDLE_DROP:
                candidate.logout()
            else:
                conn = candidate
    if conn is not None and time.monotonic() - conn.last_used > IDLE_CHECK and not conn.noop():
        conn.logout()
        conn = None
    if conn is None:
        conn = connector(server)
    broken = False
    try:
        yield conn
    except (OSError, imaplib.IMAP4.abort, ssl.SSLError) as exc:
        broken = True
        raise MailServerError(f"The connection to the mail server broke: {exc.__class__.__name__}", code="MAIL_SERVER_UNREACHABLE") from exc
    except imaplib.IMAP4.error as exc:
        broken = True
        raise MailServerError(f"The mail server answered with an error: {str(exc)[:150]}", code="MAIL_SERVER_REFUSED") from exc
    finally:
        if broken:
            conn.logout()
        else:
            conn.last_used = time.monotonic()
            try:
                conn.client.untagged_responses.clear()
            except AttributeError:
                pass
            with _lock:
                idle = _pool.setdefault(key, [])
                if len(idle) < POOL_SIZE:
                    idle.append(conn)
                else:
                    conn.logout()


def forget(server: Server) -> None:
    """Close the pooled connections of a server whose settings changed."""
    with _lock:
        for conn in _pool.pop(_key(server), []):
            conn.logout()
