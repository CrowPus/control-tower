"""Page speed, timings, errors and crashes from browsers and the phone app
(the admin app, and later plugins).

Nothing personal is kept: pages become templates (`/applications/:id`), error messages
lose e-mail addresses, links, numbers and tokens, and only a coarse device class, OS and
browser family come from the user agent. No account, no IP address.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import ErrorGroup, TelemetryEvent

APPS = {"admin"}
KINDS = {"vital", "timing", "error", "crash"}
VITALS = {"LCP", "INP", "CLS", "TTFB", "FCP"}
MAX_EVENTS = 30


class TelemetryEventIn(BaseModel):
    kind: str = Field(max_length=12)
    name: str = Field(default="", max_length=40)
    page: str = Field(default="", max_length=300)
    value: float = 0.0
    type: str = Field(default="", max_length=120)
    message: str = Field(default="", max_length=1000)
    stack: str = Field(default="", max_length=4000)


class TelemetryIn(BaseModel):
    app: str = Field(max_length=16)
    version: str = Field(default="", max_length=40)
    device: str = Field(default="", max_length=10)
    os: str = Field(default="", max_length=20)
    events: list[TelemetryEventIn] = Field(default_factory=list, max_length=MAX_EVENTS)


_ID_SEGMENT = re.compile(r"/(?:\d+|EPL-[A-Z0-9]{4}-[A-Z0-9]{2}|[0-9a-f]{8}-[0-9a-f-]{27,}|[A-Za-z0-9_-]{24,})(?=/|$)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_URL = re.compile(r"https?://\S+")
_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_NUMBER = re.compile(r"\d+")


def page_template(raw: str) -> str:
    path = (raw or "").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    return _ID_SEGMENT.sub("/:id", path)[:120]


def scrub(message: str) -> str:
    text = _EMAIL.sub("[email]", message or "")
    text = _URL.sub("[url]", text)
    text = _TOKEN.sub("[token]", text)
    text = _NUMBER.sub("#", text)
    return " ".join(text.split())[:300]


def _top_frame(stack: str) -> str:
    for line in (stack or "").splitlines():
        line = line.strip()
        if line and not line.lower().startswith(("error", "typeerror", "referenceerror")):
            # Without line and column numbers and query strings: the same bug after a rebuild stays one group.
            return re.sub(r"[?#][^\s)]*|:\d+(:\d+)?", "", line)[:200]
    return ""


def fingerprint(app: str, error_type: str, message: str, stack: str) -> str:
    return hashlib.sha256(f"{app}|{error_type}|{message}|{_top_frame(stack)}".encode()).hexdigest()[:32]


def from_user_agent(agent: str) -> tuple[str, str, str]:
    """(device, os, browser), coarse."""
    ua = (agent or "").lower()
    device = "tablet" if "ipad" in ua or ("android" in ua and "mobile" not in ua) else "phone" if any(k in ua for k in ("iphone", "android", "mobile")) else "desktop"
    os_name = ("ios" if any(k in ua for k in ("iphone", "ipad", "ios")) else "android" if "android" in ua else "windows" if "windows" in ua
               else "macos" if "mac os" in ua or "macintosh" in ua else "linux" if "linux" in ua else "other")
    browser = ("edge" if "edg/" in ua else "chrome" if "chrome/" in ua or "crios" in ua else "firefox" if "firefox" in ua or "fxios" in ua
               else "safari" if "safari" in ua else "app" if "expo" in ua or "okhttp" in ua or "cfnetwork" in ua else "other")
    return device, os_name, browser


def parse(raw: bytes) -> TelemetryIn | None:
    import json

    try:
        data = TelemetryIn.model_validate(json.loads(raw.decode("utf-8", "replace")))
    except (ValueError, ValidationError):
        return None
    return data if data.app in APPS else None


def store(session: Session, data: TelemetryIn, user_agent: str, *, force_app: str | None = None) -> int:
    """Keep what is valid; ignore the rest. Returns the number of events kept."""
    app = force_app or data.app
    device, os_name, browser = from_user_agent(user_agent)
    if data.device in ("phone", "tablet", "desktop"):
        device = data.device
    if data.os:
        os_name = re.sub(r"[^a-z0-9.]", "", data.os.lower())[:20]
    version = re.sub(r"[^A-Za-z0-9.+-]", "", data.version)[:40]
    now = datetime.now(UTC)
    kept = 0
    for event in data.events:
        if event.kind not in KINDS:
            continue
        name = re.sub(r"[^A-Za-z0-9_.-]", "", event.name)[:40]
        if event.kind == "vital" and name not in VITALS:
            continue
        value = float(event.value)
        if not (0 <= value <= (10 if name == "CLS" else 300_000)):
            continue
        page = page_template(event.page)
        fp = ""
        if event.kind in ("error", "crash"):
            error_type = re.sub(r"[^A-Za-z0-9_.]", "", event.type)[:80] or "Error"
            message = scrub(event.message)
            fp = fingerprint(app, error_type, message, event.stack)
            _group(session, fp, app=app, kind=event.kind, error_type=error_type, message=message, page=page, version=version, now=now)
            name = name or error_type[:40]
        session.add(TelemetryEvent(created_at=now, app=app, kind=event.kind, name=name, page=page, value=round(value, 3), device=device,
                                   os=os_name, browser=browser, version=version, fingerprint=fp))
        kept += 1
    return kept


def _group(session: Session, fp: str, *, app: str, kind: str, error_type: str, message: str, page: str, version: str, now: datetime) -> None:
    group = session.scalars(select(ErrorGroup).where(ErrorGroup.fingerprint == fp)).first()
    if group is None:
        session.add(ErrorGroup(fingerprint=fp, app=app, kind=kind, error_type=error_type, message=message, page=page, first_seen=now, last_seen=now,
                               count=1, versions=[version] if version else [], status="open"))
        session.flush()
        return
    group.count += 1
    group.last_seen = now
    if version and version not in (group.versions or []):
        group.versions = [*(group.versions or [])[-9:], version]
    if group.status == "resolved":
        # It came back after being marked fixed: open again.
        group.status = "open"


def summary(row: Any) -> dict[str, Any]:
    return {"fingerprint": row.fingerprint, "app": row.app, "kind": row.kind, "type": row.error_type, "message": row.message, "page": row.page,
            "first_seen": row.first_seen, "last_seen": row.last_seen, "count": row.count, "versions": row.versions or [], "status": row.status}
