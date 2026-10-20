"""Nia's browser (docs/pivot/NIA_BROWSER.md): a headless Chromium behind a small internal API. The worker drives it
for Nia, step by step; the admin shows its screen to the owner and passes the owner's own clicks and keys when the
owner takes over. It decides nothing: which action is allowed is the backend's business.

Safety of its own: every request needs the token derived from the server's secret; every page request (and every
navigation) to a private, loopback or internal address, or to a site the owner blocked, is refused inside the browser;
no downloads; at most MAX_SESSIONS at once, closed after IDLE_MINUTES without use. Sign-ins are kept between sessions
only for the sites the owner chose (`keep`), in this container's own volume."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Response
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright
from pydantic import BaseModel

WIDTH, HEIGHT = 1280, 800
MAX_SESSIONS = 2
IDLE_MINUTES = 30
STATE = Path(os.environ.get("BROWSER_STATE", "/data/state.json"))
TOKEN = hmac.new(os.environ.get("ENCRYPTION_SECRET", "").encode(), b"nia-browser", hashlib.sha256).hexdigest()
ALLOWED_SCHEMES = {"http", "https", "data", "blob", "about"}
# Nia's pointer, drawn on the page where she points or clicks, so the owner sees what she does (never takes clicks).
CURSOR_JS = """(([x, y, click]) => {
  let c = document.getElementById('__nia_cursor');
  if (!c) {
    c = document.createElement('div'); c.id = '__nia_cursor'; c.setAttribute('aria-hidden', 'true');
    c.style.cssText = 'position:fixed;left:0;top:0;width:24px;height:24px;z-index:2147483647;pointer-events:none;transition:transform .25s ease';
    c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2l6.5 18 2.6-7.4L21 10z" fill="#0f6b5c" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
  }
  c.style.transform = 'translate(' + (x - 4) + 'px,' + (y - 2) + 'px)';
  if (click) {
    const r = document.createElement('div');
    r.style.cssText = 'position:fixed;left:' + (x - 14) + 'px;top:' + (y - 14) + 'px;width:28px;height:28px;border-radius:50%;border:3px solid #0f6b5c;z-index:2147483646;pointer-events:none;transition:all .5s ease';
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 700);
  }
})"""
# The browser says it is an ordinary Chrome (as the owner's own would): sites that refuse "HeadlessChrome" with a captcha
# on every search would otherwise need the owner each time.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _host_ok(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unknown names fail by themselves
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def _blocked(host: str, blocked: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == b or host.endswith("." + b) for b in blocked)


@dataclass
class Session:
    id: str
    context: BrowserContext
    page: Page
    blocked: list[str]
    keep: list[str]
    used: float = field(default_factory=time.monotonic)
    hosts: dict[str, bool] = field(default_factory=dict)


SESSIONS: dict[str, Session] = {}
BROWSER: dict[str, Browser] = {}


async def _guard(session: Session, route: Route) -> None:
    url = route.request.url
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        await route.abort("blockedbyclient")
        return
    host = (parts.hostname or "").lower()
    if parts.scheme in ("http", "https"):
        if not host or _blocked(host, session.blocked):
            await route.abort("blockedbyclient")
            return
        if host not in session.hosts:
            session.hosts[host] = await asyncio.get_running_loop().run_in_executor(None, _host_ok, host)
        if not session.hosts[host]:
            await route.abort("blockedbyclient")
            return
    await route.continue_()


def _load_state(keep: list[str]) -> dict[str, Any] | None:
    if not keep or not STATE.exists():
        return None
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None
    cookies = [c for c in data.get("cookies", []) if _blocked(str(c.get("domain", "")).lstrip("."), keep)]
    origins = [o for o in data.get("origins", []) if _blocked(urlsplit(str(o.get("origin", ""))).hostname or "", keep)]
    return {"cookies": cookies, "origins": origins}


async def _save_state(session: Session) -> None:
    """Keep the sign-ins of the owner's chosen sites; forget everything else (also sites the owner took off the list).
    The session started with those sign-ins loaded, so what it has now is the whole up-to-date set."""
    try:
        data = await session.context.storage_state()
    except Exception:  # noqa: BLE001 - a closed context has nothing to keep
        return
    keep = session.keep

    def mine(host: str) -> bool:
        return bool(keep) and _blocked(host.lstrip("."), keep)

    kept = {"cookies": [c for c in data.get("cookies", []) if mine(str(c.get("domain", "")))],
            "origins": [o for o in data.get("origins", []) if mine(urlsplit(str(o.get("origin", ""))).hostname or "")]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept))  # noqa: ASYNC240 - a few kilobytes at the end of a session
    tmp.replace(STATE)


async def _close(session_id: str) -> None:
    session = SESSIONS.pop(session_id, None)
    if session is not None:
        await _save_state(session)
        with suppress(Exception):
            await session.context.close()


async def _reaper() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for sid in [s.id for s in SESSIONS.values() if now - s.used > IDLE_MINUTES * 60]:
            await _close(sid)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    async with async_playwright() as pw:
        BROWSER["chromium"] = await pw.chromium.launch(args=["--disable-dev-shm-usage", "--no-first-run", "--disable-background-networking",
                                                                         "--disable-blink-features=AutomationControlled"])
        task = asyncio.create_task(_reaper())
        try:
            yield
        finally:
            task.cancel()
            for sid in list(SESSIONS):
                await _close(sid)
            await BROWSER["chromium"].close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _auth(token: str | None) -> None:
    if not TOKEN or not token or not hmac.compare_digest(token, TOKEN):
        raise HTTPException(status_code=401, detail="token")


def _get(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session")
    session.used = time.monotonic()
    return session


async def _view(session: Session, *, shot: bool = True) -> dict[str, Any]:
    page = session.page
    with suppress(Exception):
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    out: dict[str, Any] = {"url": page.url, "title": ""}
    with suppress(Exception):
        out["title"] = (await page.title())[:200]
    if shot:
        out["screenshot"] = base64.b64encode(await page.screenshot(type="png")).decode()
    return out


async def _cursor(page: Page, x: int, y: int, click: bool) -> None:
    with suppress(Exception):
        await page.evaluate(CURSOR_JS, [x, y, click])


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": "chromium" in BROWSER, "sessions": len(SESSIONS)}


class Open(BaseModel):
    id: str
    blocked: list[str] = []
    keep: list[str] = []
    url: str = ""


@app.post("/sessions")
async def open_session(body: Open, x_browser_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_browser_token)
    if body.id in SESSIONS:
        # The same window, a new task: it stays open between tasks.
        session = SESSIONS[body.id]
        session.used = time.monotonic()
        if body.url:
            with suppress(Exception):
                await session.page.goto(body.url, timeout=20000)
        return await _view(session)
    if len(SESSIONS) >= MAX_SESSIONS:
        raise HTTPException(status_code=429, detail="busy")
    context = await BROWSER["chromium"].new_context(viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=False, locale="de-DE", user_agent=USER_AGENT,
                                                    timezone_id="Europe/Berlin", storage_state=_load_state(body.keep))  # type: ignore[arg-type]
    page = await context.new_page()
    session = Session(id=body.id, context=context, page=page, blocked=[b.lower() for b in body.blocked], keep=[k.lower() for k in body.keep])
    await context.route("**/*", lambda route: _guard(session, route))

    def follow(new_page: Page) -> None:
        session.page = new_page  # a link that opens a new tab: Nia follows it

    context.on("page", follow)
    SESSIONS[body.id] = session
    if body.url:
        with suppress(Exception):
            await page.goto(body.url, timeout=20000)
    return await _view(session)


class Act(BaseModel):
    action: str
    x: int | None = None
    y: int | None = None
    text: str = ""
    url: str = ""
    keys: str = ""
    direction: str = "down"
    amount: int = 600
    press_enter: bool = False
    clear: bool = False
    seconds: float = 2.0


@app.post("/sessions/{session_id}/act")
async def act(session_id: str, body: Act, x_browser_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_browser_token)
    session = _get(session_id)
    page = session.page
    error = ""
    try:
        if body.action == "navigate":
            await page.goto(body.url, timeout=20000)
        elif body.action in ("click", "double_click", "hover"):
            if body.x is None or body.y is None:
                raise ValueError("x and y")
            await _cursor(page, body.x, body.y, body.action != "hover")
            if body.action == "hover":
                await page.mouse.move(body.x, body.y)
            else:
                await page.mouse.click(body.x, body.y, click_count=2 if body.action == "double_click" else 1)
        elif body.action == "type":
            if body.x is not None and body.y is not None:
                await _cursor(page, body.x, body.y, True)
                await page.mouse.click(body.x, body.y)
            if body.clear:
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
            await page.keyboard.type(body.text, delay=12)
            if body.press_enter:
                await page.keyboard.press("Enter")
        elif body.action == "key":
            await page.keyboard.press(body.keys)
        elif body.action == "scroll":
            if body.x is not None and body.y is not None:
                await page.mouse.move(body.x, body.y)
            dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.get(body.direction, (0, 1))
            await page.mouse.wheel(dx * body.amount, dy * body.amount)
        elif body.action == "back":
            await page.go_back(timeout=15000)
        elif body.action == "forward":
            await page.go_forward(timeout=15000)
        elif body.action == "wait":
            await asyncio.sleep(max(0.5, min(body.seconds, 10)))
        elif body.action == "screenshot":
            pass
        else:
            raise ValueError(f"unknown action {body.action}")
        await asyncio.sleep(0.6)
    except Exception as exc:  # noqa: BLE001 - the caller reads what went wrong and goes on
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return {**await _view(session), "error": error}


_INSPECT = """([x, y]) => {
  const pick = (el) => {
    if (!el) return null;
    const hit = el.closest('button, a, input, select, textarea, label, [role=button], [role=link], [role=checkbox], [onclick], [contenteditable=true]') || el;
    let target = hit;
    if (hit.tagName === 'LABEL' && hit.control) target = hit.control;
    const form = target.form || target.closest('form');
    const tag = target.tagName.toLowerCase();
    const type = (target.getAttribute('type') || '').toLowerCase();
    const text = (target.innerText || target.value || target.getAttribute('aria-label') || target.getAttribute('title') || target.getAttribute('placeholder') || '').trim().slice(0, 160);
    const submit = (tag === 'button' && (type === '' || type === 'submit') && !!form) || (tag === 'input' && (type === 'submit' || type === 'image'));
    const auto = (target.getAttribute('autocomplete') || '').toLowerCase();
    const name = (target.getAttribute('name') || '').toLowerCase();
    const hint = ((target.getAttribute('placeholder') || '') + ' ' + (target.getAttribute('aria-label') || '')).toLowerCase();
    const search = type === 'search' || target.getAttribute('role') === 'searchbox' || ['q', 'query', 'search', 's', 'suche', 'keywords'].includes(name)
      || (!!form && form.getAttribute('role') === 'search') || /\b(such|search)/.test(hint);
    return {tag, type, role: target.getAttribute('role') || '', text, in_form: !!form, is_submit: submit, is_search: search,
            is_password: type === 'password' || auto.includes('password'), is_file: type === 'file',
            href: target.href || '', name: target.getAttribute('name') || ''};
  };
  return {at: pick(document.elementFromPoint(x, y)), focused: pick(document.activeElement && document.activeElement !== document.body ? document.activeElement : null)};
}"""


@app.get("/sessions/{session_id}/inspect")
async def inspect(session_id: str, x: int = -1, y: int = -1, x_browser_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_browser_token)
    session = _get(session_id)
    try:
        return dict(await session.page.evaluate(_INSPECT, [x, y]))
    except Exception as exc:  # noqa: BLE001
        return {"at": None, "focused": None, "error": type(exc).__name__}


@app.get("/sessions/{session_id}/frame")
async def frame(session_id: str, x_browser_token: str | None = Header(default=None)) -> Response:
    _auth(x_browser_token)
    session = _get(session_id)
    try:
        jpeg = await session.page.screenshot(type="jpeg", quality=60)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=type(exc).__name__) from exc
    return Response(jpeg, media_type="image/jpeg", headers={"X-Page-Url": session.page.url[:1000], "Cache-Control": "no-store"})


class Rules(BaseModel):
    blocked: list[str] = []
    keep: list[str] = []


@app.put("/sessions/{session_id}/rules")
async def rules(session_id: str, body: Rules, x_browser_token: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(x_browser_token)
    session = _get(session_id)
    session.blocked, session.keep = [b.lower() for b in body.blocked], [k.lower() for k in body.keep]
    return {"ok": True}


@app.delete("/sessions/{session_id}")
async def close(session_id: str, x_browser_token: str | None = Header(default=None)) -> dict[str, bool]:
    _auth(x_browser_token)
    await _close(session_id)
    return {"ok": True}
