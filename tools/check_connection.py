#!/usr/bin/env python3
"""Check a project's connection for Control Tower, from the project's side — before telling the owner it is done.

    CONTROL_TOWER_SECRET=<the secret the project runs with> python3 check_connection.py http://localhost:3000/control-tower

Only Python 3's standard library. It checks, in this order, and says PASS or FAIL for each with what to fix:
unsigned and wrongly signed requests are refused; the signed manifest is valid; /metrics answers with a number for every
metric; every tool without required parameters answers with a JSON object. It changes nothing: it never calls an
action's execute. The protocol: docs/CONNECT-A-PROJECT.md in the Control Tower repository.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

KEY = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
TYPES = {"string", "integer", "number", "boolean", "array", "object"}
results: list[tuple[bool, str]] = []


def report(ok: bool, text: str) -> bool:
    results.append((ok, text))
    print(f"{'PASS' if ok else 'FAIL'}  {text}")
    return ok


def sign(secret: str, path: str, body: bytes) -> str:
    t = int(time.time())
    return f"t={t},v1={hmac.new(secret.encode(), f'{t}.{path}.'.encode() + body, hashlib.sha256).hexdigest()}"


def call(method: str, url: str, secret: str | None, body: dict | None = None) -> tuple[int, object]:
    raw = json.dumps(body).encode() if body is not None else b""
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if secret is not None:
        headers["X-Control-Tower-Signature"] = sign(secret, urlparse(url).path, raw)
    request = urllib.request.Request(url, data=raw or None, method=method, headers=headers)  # noqa: S310 - main() accepts only http(s)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - http(s) only, see main()
            status, text = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read()
    try:
        return status, json.loads(text) if text else {}
    except ValueError:
        return status, text[:200].decode(errors="replace")


def check_manifest(m: object) -> list[str]:
    if not isinstance(m, dict):
        return ["the manifest is not a JSON object"]
    problems = []
    if not str(m.get("api", "")).startswith("1."):
        problems.append('"api" must be "1.0"')
    if not KEY.match(str(m.get("key", ""))):
        problems.append('"key": lowercase letters, digits and _, starting with a letter (the key chosen in Control Tower)')
    for field in ("name", "version"):
        if not m.get(field):
            problems.append(f'"{field}" is missing')
    for kind in ("tools", "actions", "metrics", "templates"):
        if not isinstance(m.get(kind, []), list):
            problems.append(f'"{kind}" must be a list')
    for tool in m.get("tools") or []:
        if not KEY.match(str(tool.get("key", ""))) or not tool.get("description"):
            problems.append(f"tool {tool.get('key')!r}: needs a key (lowercase, _) and a description")
        for name, p in (tool.get("params") or {}).items():
            if p.get("type") not in TYPES:
                problems.append(f"tool {tool.get('key')} param {name}: type must be one of {', '.join(sorted(TYPES))}")
    for action in m.get("actions") or []:
        if action.get("ceiling", "L3") not in ("L2", "L3"):
            problems.append(f"action {action.get('key')}: ceiling is L2 or L3")
        if action.get("target") and action["target"] not in (action.get("fields") or {}):
            problems.append(f"action {action.get('key')}: target {action['target']!r} is not one of its fields")
    for metric in m.get("metrics") or []:
        if metric.get("agg", "sum") not in ("sum", "avg", "last", "max"):
            problems.append(f"metric {metric.get('key')}: agg is sum, avg, last or max")
    if not isinstance(m.get("personal", []), list):
        problems.append('"personal" must be a list of field names')
    return problems


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith(("http://", "https://")):
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    secret = os.environ.get("CONTROL_TOWER_SECRET", "")
    if not secret:
        print("Set CONTROL_TOWER_SECRET to the secret the project is running with.")
        return 2
    print(f"Checking {base}\n")
    try:
        status, _ = call("GET", f"{base}/manifest", None)
    except (urllib.error.URLError, OSError) as error:
        report(False, f"cannot reach {base}/manifest ({error}). Is the project running, and is this the right address?")
        return 1
    if status == 404:
        report(False, "the project answers, but has no /manifest under this address: are the routes registered (and the secret set, which turns them on)?")
        return 1
    report(status == 401, f"an unsigned request is refused (got {status}, want 401)")
    status, _ = call("GET", f"{base}/manifest", "not-the-secret")
    report(status == 401, f"a wrongly signed request is refused (got {status}, want 401)")
    status, manifest = call("GET", f"{base}/manifest", secret)
    if not report(status == 200, f"the signed manifest is answered (got {status}, want 200)"):
        print("\nIf it is 401: the secret here is not the one the project runs with, or the path the project verifies differs from the address called.")
        return 1
    problems = check_manifest(manifest)
    report(not problems, "the manifest is valid" + ("" if not problems else ": " + "; ".join(problems)))
    m = manifest if isinstance(manifest, dict) else {}
    print(f"\n      {m.get('name')} {m.get('version')}: {len(m.get('tools') or [])} tools, {len(m.get('actions') or [])} actions, "
          f"{len(m.get('metrics') or [])} numbers, {len(m.get('templates') or [])} starter agents; personal: {', '.join(m.get('personal') or []) or 'none'}\n")
    if m.get("metrics"):
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        status, data = call("GET", f"{base}/metrics?since={quote(since)}", secret)
        values = data.get("values") if isinstance(data, dict) else None
        if report(status == 200 and isinstance(values, dict), f"/metrics answers with values (got {status})") and isinstance(values, dict):
            for metric in m["metrics"]:
                v = values.get(metric.get("key"))
                ok = isinstance(v, (int, float)) and not isinstance(v, bool) or (isinstance(v, dict) and all(isinstance(x, (int, float)) for x in v.values()))
                report(ok, f"metric {metric.get('key')} is a number (or numbers per dimension): {v!r}")
    for tool in m.get("tools") or []:
        if any(p.get("required") for p in (tool.get("params") or {}).values()):
            print(f"      (tool {tool.get('key')} has required parameters: not called)")
            continue
        status, data = call("POST", f"{base}/tools/{tool.get('key')}", secret, {"args": {}, "agent": "connection_check"})
        size = len(json.dumps(data)) if isinstance(data, dict) else 0
        report(status == 200 and isinstance(data, dict), f"tool {tool.get('key')} answers with a JSON object ({status}, {size} characters)")
        if size > 60_000:
            report(False, f"tool {tool.get('key')}'s answer is over 60,000 characters: give it a smaller default limit")
    for action in m.get("actions") or []:
        print(f"      action {action.get('key')} ({action.get('ceiling', 'L3')}): not called — make sure execute refuses an approval_id it carried out already (409)")
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n{'All checks passed.' if not failed else f'{failed} check(s) failed.'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
