"""The QA checks: the owner's pages answer with what they should hold, they send their security headers and their
certificates are valid; and the admin itself answers. Read-only: nothing is created or changed.

The pages (name, address, the text the answer must contain) and the rules (how often the checks run by themselves,
after how many failed runs in a row a check counts as broken, what counts as slow, how many days before a certificate
ends it is a warning) are the owner's settings (`qa`, Admin → Product → Checks). A plugin may add its own journey.
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import QaRun
from .. import platform

KEEP_DAYS = 30


class QaPage(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(pattern=r"^https?://", max_length=500)
    # Text the answer must contain (empty: any answer below 400 passes).
    contains: str = Field(default="", max_length=200)


MAX_PAGES = 30


@dataclass
class Check:
    name: str
    url: str
    ok: bool
    status: int
    ms: int
    detail: str = ""
    group: str = "pages"  # pages | security
    slow: bool = False


@dataclass(frozen=True)
class Rules:
    every_minutes: int | None = None
    fail_after: int | None = None
    slow_ms: int | None = None
    tls_warn_days: int | None = None


RULE_FIELDS = ("every_minutes", "fail_after", "slow_ms", "tls_warn_days")


def rules(session: Session) -> Rules:
    raw = platform.get(session, "qa") or {}
    return Rules(**{k: int(raw[k]) for k in RULE_FIELDS if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool) and raw[k] > 0})


def save_rules(session: Session, values: dict[str, int | None]) -> None:
    raw = dict(platform.get(session, "qa") or {})
    for key in RULE_FIELDS:
        if values.get(key) is None:
            raw.pop(key, None)
        else:
            raw[key] = int(values[key])  # type: ignore[arg-type]
    platform.put(session, "qa", raw)


# --- the owner's pages ---------------------------------------------------------------------------------------
def pages(session: Session) -> list[QaPage]:
    raw = platform.get(session, "qa") or {}
    out = []
    for item in raw.get("pages") or []:
        try:
            out.append(QaPage.model_validate(item))
        except ValueError:
            continue
    return out[:MAX_PAGES]


def save_pages(session: Session, items: list[QaPage]) -> None:
    raw = dict(platform.get(session, "qa") or {})
    raw["pages"] = [i.model_dump() for i in items[:MAX_PAGES]]
    platform.put(session, "qa", raw)


def targets(session: Session) -> list[tuple[str, str, str, str]]:
    """(name, address, text the answer must contain, group): the admin itself, then the owner's pages."""
    admin = (settings().admin_public_base_url or "").rstrip("/")
    out: list[tuple[str, str, str, str]] = [("Admin", f"{admin}/admin-api/health", "ok", "pages")] if admin else []
    return out + [(p.name, p.url, p.contains, "pages") for p in pages(session)]


def _get(http: httpx.Client, name: str, url: str, needle: str, group: str, slow_ms: int | None) -> tuple[Check, httpx.Response | None]:
    started = time.perf_counter()
    try:
        response = http.get(url)
    except httpx.HTTPError as exc:
        return Check(name, url, False, 0, int((time.perf_counter() - started) * 1000), exc.__class__.__name__, group), None
    ms = int((time.perf_counter() - started) * 1000)
    ok = response.status_code < 400 and (not needle or needle in response.text)
    detail = "" if ok else f"missing “{needle}”" if response.status_code < 400 else f"HTTP {response.status_code}"
    return Check(name, url, ok, response.status_code, ms, detail, group, slow=slow_ms is not None and ms > slow_ms), response


def _headers(name: str, url: str, response: httpx.Response) -> Check:
    """Production pages must send HSTS and a content security policy (or at least frame protection)."""
    missing = [h for h in ("strict-transport-security",) if h not in response.headers]
    if "content-security-policy" not in response.headers and "x-frame-options" not in response.headers:
        missing.append("content-security-policy")
    return Check(f"Security headers: {name}", url, not missing, response.status_code, 0, ("missing " + ", ".join(missing)) if missing else "", "security")


def certificate_days(host: str, port: int = 443, timeout: float = 8.0) -> int:
    """Days until the host's TLS certificate ends."""
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
        cert = tls.getpeercert()
    if not cert:
        raise ValueError("no certificate")
    ends = datetime.strptime(str(cert["notAfter"]), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    return (ends - datetime.now(UTC)).days


def _certificates(urls: list[str], warn_days: int | None, probe: Any = certificate_days) -> list[Check]:
    hosts = sorted({urlsplit(u).hostname or "" for u in urls if u and u.startswith("https://")} - {""})
    out = []
    for host in hosts:
        try:
            days = int(probe(host))
            ok = days > (warn_days or 0)
            out.append(Check(f"Certificate: {host}", f"https://{host}", ok, 200, 0, f"ends in {days} days", "security"))
        except (OSError, ssl.SSLError, ValueError, KeyError) as exc:
            out.append(Check(f"Certificate: {host}", f"https://{host}", False, 0, 0, exc.__class__.__name__, "security"))
    return out


def run(session: Session, *, client: httpx.Client | None = None, cert_probe: Any = certificate_days) -> list[dict[str, Any]]:
    the_rules = rules(session)
    own = client is None
    http = client or httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "ControlTower-QA/1.0"})
    results: list[Check] = []
    production = settings().is_production
    wanted = targets(session)
    try:
        for name, url, needle, group in wanted:
            check, response = _get(http, name, url, needle, group, the_rules.slow_ms)
            results.append(check)
            if production and response is not None and url.startswith("https://") and name != "Admin":
                results.append(_headers(name, url, response))
        if production:
            results += _certificates([url for _n, url, _c, _g in wanted], the_rules.tls_warn_days, cert_probe)
    finally:
        if own:
            http.close()
    return [asdict(r) for r in results]


# --- runs and what changed ----------------------------------------------------------------------------------------
def record(session: Session, results: list[dict[str, Any]], trigger: str) -> QaRun:
    now = datetime.now(UTC)
    row = QaRun(started_at=now, trigger=trigger, checks=results, total=len(results), failed=sum(1 for r in results if not r["ok"]),
                slowest_ms=max((int(r["ms"]) for r in results), default=0))
    session.add(row)
    session.execute(delete(QaRun).where(QaRun.started_at < now - timedelta(days=KEEP_DAYS)))
    session.flush()
    return row


def streaks(session: Session, runs: int = 50) -> dict[str, dict[str, Any]]:
    """Per check: failed runs in a row (newest first), since when it fails, and its pass rate over the recent runs."""
    rows = list(session.execute(select(QaRun).order_by(QaRun.started_at.desc()).limit(runs)).scalars())
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        for check in row.checks:
            s = out.setdefault(check["name"], {"failing_runs": 0, "failing_since": None, "streak_open": True, "passed": 0, "seen": 0, "last": check})
            s["seen"] += 1
            s["passed"] += 1 if check["ok"] else 0
            if s["streak_open"]:
                if check["ok"]:
                    s["streak_open"] = False
                else:
                    s["failing_runs"] += 1
                    s["failing_since"] = row.started_at.isoformat()
    for s in out.values():
        s.pop("streak_open")
        s["pass_rate"] = round(s["passed"] / s["seen"], 3) if s["seen"] else None
    return out


def history(session: Session, runs: int = 20) -> dict[str, Any]:
    rows = list(session.execute(select(QaRun).order_by(QaRun.started_at.desc()).limit(runs)).scalars())
    per = streaks(session)
    return {
        "runs": [{"at": r.started_at.isoformat(), "trigger": r.trigger, "total": r.total, "failed": r.failed, "slowest_ms": r.slowest_ms} for r in rows],
        "failing_now": [{"name": n, "failing_runs": s["failing_runs"], "since": s["failing_since"], "detail": s["last"].get("detail", ""), "url": s["last"]["url"]}
                        for n, s in per.items() if s["failing_runs"]],
        "flaky": [{"name": n, "pass_rate": s["pass_rate"]} for n, s in per.items() if not s["failing_runs"] and s["pass_rate"] is not None and s["pass_rate"] < 1],
        "slow_now": [c for c in (rows[0].checks if rows else []) if c.get("slow")],
        "rules": {k: getattr(rules(session), k) for k in RULE_FIELDS},
    }


def changes(session: Session) -> tuple[list[dict[str, Any]], list[str]]:
    """After a run: checks that just reached the owner's failed runs in a row (broken), and checks that pass again after
    having been broken (recovered)."""
    fail_after = rules(session).fail_after or 1
    rows = list(session.execute(select(QaRun).order_by(QaRun.started_at.desc()).limit(fail_after + 1)).scalars())
    if not rows:
        return [], []
    per = streaks(session, runs=max(50, fail_after + 1))
    broken = [{"name": n, "url": s["last"]["url"], "status": s["last"]["status"], "detail": s["last"].get("detail", "")} for n, s in per.items()
              if s["failing_runs"] == fail_after]
    recovered = []
    if len(rows) > 1:
        before = {c["name"]: c for c in rows[1].checks}
        for check in rows[0].checks:
            if check["ok"] and check["name"] in before and not before[check["name"]]["ok"]:
                prior = [r for r in rows[1:] if not next((c["ok"] for c in r.checks if c["name"] == check["name"]), True)]
                if len(prior) >= fail_after:
                    recovered.append(check["name"])
    return broken, recovered


def due(session: Session, now: datetime | None = None) -> bool:
    every = rules(session).every_minutes
    if every is None:
        return False
    now = now or datetime.now(UTC)
    last = session.execute(select(QaRun.started_at).where(QaRun.trigger == "schedule").order_by(QaRun.started_at.desc()).limit(1)).scalar()
    return last is None or now - last >= timedelta(minutes=every) - timedelta(seconds=30)
