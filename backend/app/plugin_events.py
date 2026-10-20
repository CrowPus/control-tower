"""Where a connected project tells the team something happened (docs/PLUGINS.md → Events):

    POST /plugin-api/v1/<plugin>/events   {"events": [{"type": "signup", "payload": {...}}]}

signed with the plugin's secret (services/plugins/signing.py). Each event becomes `<plugin>.<type>` for the agents
subscribed to it; the fields the plugin marks personal are removed first. Open to the internet (outside the admin's IP
allowlist): the signature is the credential. Answers say nothing about why a request was refused.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .db import transaction
from .services.plugins import core as plugins
from .services.plugins import signing

router = APIRouter(include_in_schema=False)
PREFIX = "/plugin-api/"
# Safety rails, not business values.
MAX_BODY_BYTES = 64_000
MAX_EVENTS = 50
_TYPE = re.compile(r"^[a-z][a-z0-9_.]{0,40}$")


def _refused(status: int = 401) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": "refused"})


@router.post(PREFIX + "v1/{key}/events")
async def receive(key: str, request: Request) -> JSONResponse:
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = MAX_BODY_BYTES + 1
    if declared > MAX_BODY_BYTES:
        return _refused(413)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _refused(413)
    plugins.ensure()
    if not plugins.enabled(key) or not signing.verify(plugins.secret(key), request.url.path, body, request.headers.get(signing.HEADER, "")):
        return _refused()
    try:
        data = json.loads(body)
        events = data["events"]
        if not isinstance(events, list) or not 0 < len(events) <= MAX_EVENTS:
            raise ValueError
        clean: list[tuple[str, dict[str, Any]]] = []
        for event in events:
            type_, payload = event["type"], event.get("payload") or {}
            if not isinstance(type_, str) or not _TYPE.match(type_) or not isinstance(payload, dict):
                raise ValueError
            clean.append((type_, payload))
    except (ValueError, KeyError, TypeError):
        return JSONResponse(status_code=422, content={"error": f'Send {{"events": [{{"type": "signup", "payload": {{}}}}]}}, at most {MAX_EVENTS} events.'})
    personal = frozenset(p.lower() for p in plugins.LOADED[key].plugin.personal)
    with transaction(write=True) as session:
        for type_, payload in clean:
            plugins.emit_event(session, key, type_, plugins.mask(payload, personal))
    return JSONResponse(status_code=202, content={"received": len(clean)})
