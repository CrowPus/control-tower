"""The Agent Gateway: the only door for connected outside agents.

* **MCP** over HTTP at `/agent-gateway/mcp` (JSON-RPC 2.0; answers as JSON, no server stream): `initialize`, `tools/list`,
  `tools/call`, `ping`. Claude Code, Claude.ai, ChatGPT connectors and Gemini CLI speak it.
* The same tools over plain HTTPS: `GET /agent-gateway/tools`, `POST /agent-gateway/tools/{name}`.
* **Sign-in**: a bearer credential (`ct_agent_…`, issued by the owner), or OAuth 2.1 with PKCE for connectors that require
  it (Claude.ai, ChatGPT): the connector registers itself, the person connecting pastes the agent's credential on the install's
  page, and the connector gets short-lived access tokens for that agent.

Every call is the agent's, recorded; what changes something becomes a proposal in Approvals.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select

from ...config import settings
from ...core.net import client_ip
from ...db import transaction
from ...models import OutsideAgent, OutsideOAuthClient, OutsideOAuthGrant
from . import core

router = APIRouter(include_in_schema=False)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
CODE_MINUTES = 10
ACCESS_MINUTES = 60
REFRESH_DAYS = 30
MAX_CLIENTS = 500  # registered connectors kept (a safety rail against flooding the open registration)
TYPES = {"OBJECT": "object", "STRING": "string", "INTEGER": "integer", "NUMBER": "number", "BOOLEAN": "boolean", "ARRAY": "array"}


def _db() -> Any:
    return transaction(write=True)


def _base(request: Request) -> str:
    """This server's public address (behind the proxy the scheme reads http)."""
    host = request.headers.get("host") or request.url.netloc
    scheme = "http" if host.startswith(("localhost", "127.0.0.1", "testserver")) else "https"
    return f"{scheme}://{host}"


def _json_schema(node: Any) -> Any:
    """The team's declarations (Gemini's upper-case types) as JSON Schema."""
    if isinstance(node, dict):
        out = {k: _json_schema(v) for k, v in node.items()}
        if isinstance(node.get("type"), str):
            out["type"] = TYPES.get(node["type"], node["type"].lower())
        return out
    if isinstance(node, list):
        return [_json_schema(v) for v in node]
    return node


def _unauthorized(request: Request, message: str, status: int = 401) -> JSONResponse:
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = f'Bearer resource_metadata="{_base(request)}/.well-known/oauth-protected-resource"'
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def _agent(request: Request) -> OutsideAgent | JSONResponse:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return _unauthorized(request, "Sign in with the agent's credential (Authorization: Bearer …).")
    token = auth[7:].strip()
    try:
        with _db() as session:
            row = core.authenticate(session, token, client_ip(request))
            session.expunge(row)
            return row
    except core.Refused as exc:
        return _unauthorized(request, str(exc), exc.status)


def _run(request: Request, row: OutsideAgent, method: str, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """One tool call within the agent's limits, recorded."""
    ip = client_ip(request)
    try:
        with _db() as session:
            fresh = session.get(OutsideAgent, row.id)
            if fresh is None:
                raise core.Refused("This agent no longer exists.", 401)
            core.check_limits(session, fresh)
    except core.Refused as exc:
        with _db() as session:
            fresh = session.get(OutsideAgent, row.id)
            if fresh is not None:
                core.record(session, fresh, via="gateway", method=method, tool=name, ok=False, error=str(exc), args=args, ip=ip)
        return {"error": str(exc)}, False
    result, ms = core.timed(lambda: core.call_tool(_db, row, name, args))
    ok = "error" not in result
    with _db() as session:
        fresh = session.get(OutsideAgent, row.id)
        if fresh is not None:
            core.record(session, fresh, via="gateway", method=method, tool=name, ok=ok, error=str(result.get("error", ""))[:300] if not ok else "",
                        args=args, result_chars=len(json.dumps(result, default=str)), ip=ip, duration_ms=ms)
    return result, ok


# --- MCP ------------------------------------------------------------------------------------------------------------------
def _rpc(id_: Any, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
    if error:
        out["error"] = {"code": error[0], "message": error[1]}
    else:
        out["result"] = result
    return out


def _answer(request: Request, row: OutsideAgent, message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    id_ = message.get("id")
    params = message.get("params") or {}
    if id_ is None:  # a notification (e.g. notifications/initialized): nothing to answer
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        with _db() as session:
            fresh = session.get(OutsideAgent, row.id)
            if fresh is not None:
                core.record(session, fresh, via="gateway", method="initialize", ip=client_ip(request), args={"client": params.get("clientInfo")})
        return _rpc(id_, {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "control-tower", "title": f"{settings().app_name} — outside agent desk", "version": "1.0"},
                          "instructions": f"You are {row.name}, an outside agent at {settings().app_name}. Call my_job first. {core.house_rules()}"})
    if method == "ping":
        return _rpc(id_, {})
    if method == "tools/list":
        tools = [{"name": d["name"], "description": d["description"], "inputSchema": _json_schema(d["parameters"])} for d in core.declarations(row)]
        return _rpc(id_, {"tools": tools})
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc(id_, error=(-32602, "arguments must be an object"))
        result, ok = _run(request, row, "tools/call", name, args)
        text = json.dumps(result, ensure_ascii=False, default=str)
        return _rpc(id_, {"content": [{"type": "text", "text": text}], "isError": not ok})
    if method in ("resources/list", "prompts/list", "resources/templates/list"):
        return _rpc(id_, {method.split("/")[0]: []} if method != "resources/templates/list" else {"resourceTemplates": []})
    return _rpc(id_, error=(-32601, f"Method not found: {method}"))


@router.post("/agent-gateway/mcp")
async def mcp(request: Request) -> Response:
    row = _agent(request)
    if isinstance(row, JSONResponse):
        return row
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_rpc(None, error=(-32700, "Parse error")), status_code=400)
    messages = body if isinstance(body, list) else [body]
    if not messages or not all(isinstance(m, dict) for m in messages):
        return JSONResponse(_rpc(None, error=(-32600, "Invalid request")), status_code=400)
    from starlette.concurrency import run_in_threadpool

    answers = [a for a in [await run_in_threadpool(_answer, request, row, m) for m in messages] if a is not None]
    if not answers:
        return Response(status_code=202)
    return JSONResponse(answers if isinstance(body, list) else answers[0])


@router.get("/agent-gateway/mcp")
@router.delete("/agent-gateway/mcp")
def mcp_stream() -> Response:
    # No server-initiated stream and no server-side sessions: every request stands alone.
    return Response(status_code=405, headers={"Allow": "POST"})


# --- the same tools over plain HTTPS ---------------------------------------------------------------------------------------
@router.get("/agent-gateway/tools")
def list_tools(request: Request) -> Response:
    row = _agent(request)
    if isinstance(row, JSONResponse):
        return row
    return JSONResponse({"agent": row.name, "tools": [{"name": d["name"], "description": d["description"], "parameters": _json_schema(d["parameters"])}
                                                      for d in core.declarations(row)]})


@router.post("/agent-gateway/tools/{name}")
async def call(name: str, request: Request) -> Response:
    row = _agent(request)
    if isinstance(row, JSONResponse):
        return row
    try:
        args = await request.json()
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        return JSONResponse({"error": "The body is a JSON object of the tool's inputs."}, status_code=400)
    from starlette.concurrency import run_in_threadpool

    result, ok = await run_in_threadpool(_run, request, row, "rest", name, args)
    return JSONResponse(result, status_code=200 if ok else 400)


# --- OAuth 2.1 for connectors (Claude.ai, ChatGPT) ------------------------------------------------------------------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/agent-gateway/mcp")
def protected_resource(request: Request) -> JSONResponse:
    base = _base(request)
    return JSONResponse({"resource": f"{base}/agent-gateway/mcp", "authorization_servers": [base], "bearer_methods_supported": ["header"],
                         "resource_name": f"{settings().app_name} outside agent desk"})


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/agent-gateway/mcp")
def authorization_server(request: Request) -> JSONResponse:
    base = _base(request)
    return JSONResponse({"issuer": base, "authorization_endpoint": f"{base}/agent-gateway/oauth/authorize", "token_endpoint": f"{base}/agent-gateway/oauth/token",
                         "registration_endpoint": f"{base}/agent-gateway/oauth/register", "response_types_supported": ["code"],
                         "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
                         "token_endpoint_auth_methods_supported": ["none"]})


def _redirect_ok(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))


@router.post("/agent-gateway/oauth/register")
async def register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    uris = [str(u) for u in (body.get("redirect_uris") or []) if isinstance(u, str)]
    if not uris or not all(_redirect_ok(u) for u in uris) or len(uris) > 10:
        return JSONResponse({"error": "invalid_redirect_uri", "error_description": "https redirect addresses (or http on localhost) are needed."}, status_code=400)
    client_id = "epl_client_" + secrets.token_urlsafe(18)
    name = str(body.get("client_name") or "A connector")[:120]
    with _db() as session:
        # Registration is open (connectors register themselves), so it is capped: a flood cannot fill the table.
        from sqlalchemy import func

        if (session.execute(select(func.count()).select_from(OutsideOAuthClient)).scalar() or 0) >= MAX_CLIENTS:
            return JSONResponse({"error": "temporarily_unavailable", "error_description": "Too many registered connectors."}, status_code=429)
        session.add(OutsideOAuthClient(client_id=client_id, name=name, redirect_uris=uris, created_at=datetime.now(UTC)))
    return JSONResponse({"client_id": client_id, "client_name": name, "redirect_uris": uris, "token_endpoint_auth_method": "none",
                         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}, status_code=201)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;background:#f4f6fb;margin:0;padding:24px;color:#18181b}main{max-width:460px;margin:8vh auto;background:#fff;"
            "border-radius:14px;padding:28px;box-shadow:0 8px 24px rgba(0,0,0,.08)}h1{font-size:21px;margin:0 0 12px}input{width:100%;box-sizing:border-box;font:16px monospace;"
            "padding:12px;border:1px solid #d4d4d8;border-radius:8px}button{margin-top:14px;width:100%;padding:12px;font:600 16px system-ui;color:#fff;background:#1347b8;"
            "border:0;border-radius:8px}.muted{color:#52525b;font-size:14px}.err{color:#b91c1c}</style></head>"
            f"<body><main>{body}</main></body></html>")
    return HTMLResponse(html, status_code=status, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
                                                          "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _client(session: Any, client_id: str, redirect_uri: str) -> OutsideOAuthClient | None:
    row: OutsideOAuthClient | None = session.execute(select(OutsideOAuthClient).where(OutsideOAuthClient.client_id == client_id)).scalar_one_or_none()
    return row if row is not None and redirect_uri in row.redirect_uris else None


def _form(client: str, q: dict[str, str], error: str = "") -> str:
    hidden = "".join(f"<input type=hidden name='{escape(k)}' value='{escape(v)}'>" for k, v in q.items())
    return (f"<h1>Connect {escape(client)} to {escape(settings().app_name)}</h1><p class=muted>This connects an outside agent to {escape(settings().app_name)}. Paste the agent's credential "
            f"the owner issued (it starts with <code>ct_agent_</code>). The connection works only with the tools the owner gave this agent, "
            f"and everything it wants to change waits for the owner's approval.</p>{f'<p class=err>{escape(error)}</p>' if error else ''}"
            f"<form method=post>{hidden}<input name=credential type=password autocomplete=off required placeholder='ct_agent_…' aria-label='Agent credential'>"
            f"<button type=submit>Connect</button></form>")


@router.get("/agent-gateway/oauth/authorize")
def authorize_page(request: Request) -> HTMLResponse:
    q = {k: request.query_params.get(k, "") for k in ("client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "response_type")}
    if q["response_type"] != "code" or q["code_challenge_method"] != "S256" or not q["code_challenge"]:
        return _page("Not possible", "<h1>This sign-in cannot continue</h1><p class=muted>The connector must use the code flow with PKCE (S256).</p>", 400)
    with _db() as session:
        client = _client(session, q["client_id"], q["redirect_uri"])
        if client is None:
            return _page("Unknown connector", "<h1>Unknown connector</h1><p class=muted>This connector is not registered, or its return address does not match.</p>", 400)
        return _page(f"Connect to {settings().app_name}", _form(client.name, q))


@router.post("/agent-gateway/oauth/authorize")
async def authorize(request: Request) -> HTMLResponse:
    form = await request.form()
    q = {k: str(form.get(k) or "") for k in ("client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "response_type")}
    credential = str(form.get("credential") or "").strip()
    with _db() as session:
        client = _client(session, q["client_id"], q["redirect_uri"])
        if client is None or q["code_challenge_method"] != "S256" or not q["code_challenge"]:
            return _page("Unknown connector", "<h1>Unknown connector</h1>", 400)
        try:
            agent = core.authenticate(session, credential, client_ip(request))
        except core.Refused as exc:
            return _page(f"Connect to {settings().app_name}", _form(client.name, q, str(exc) if exc.status != 401 else "That credential is not valid."), 401)
        if not credential.startswith(core.CREDENTIAL_PREFIX):
            return _page(f"Connect to {settings().app_name}", _form(client.name, q, "Paste the agent's credential (ct_agent_…)."), 400)
        code = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        session.add(OutsideOAuthGrant(agent_id=agent.id, client_id=client.client_id, kind="code", token_hash=_hash(code), redirect_uri=q["redirect_uri"],
                                      code_challenge=q["code_challenge"], expires_at=now + timedelta(minutes=CODE_MINUTES), created_at=now))
        core.record(session, agent, via="gateway", method="oauth/authorize", ip=client_ip(request), args={"client": client.name})
        name = agent.name
    target = q["redirect_uri"] + ("&" if "?" in q["redirect_uri"] else "?") + urlencode({"code": code, **({"state": q["state"]} if q["state"] else {})})
    # A link and a refresh instead of a redirect from the form: the page's own policy allows forms only to itself.
    body = (f"<h1>Connected</h1><p class=muted>{escape(name)} is connected. Returning to the connector…</p>"
            f"<p><a href='{escape(target)}'>Continue</a></p>")
    page = _page("Connected", body)
    page.headers["Refresh"] = f"0;url={target}"
    return page


def _tokens(session: Any, agent_id: int, client_id: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    access, refresh = core.ACCESS_PREFIX + secrets.token_urlsafe(32), "epl_ort_" + secrets.token_urlsafe(32)
    session.add(OutsideOAuthGrant(agent_id=agent_id, client_id=client_id, kind="access", token_hash=_hash(access), expires_at=now + timedelta(minutes=ACCESS_MINUTES), created_at=now))
    session.add(OutsideOAuthGrant(agent_id=agent_id, client_id=client_id, kind="refresh", token_hash=_hash(refresh), expires_at=now + timedelta(days=REFRESH_DAYS), created_at=now))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_MINUTES * 60, "refresh_token": refresh}


def _grant_error(code: str, text: str) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": text}, status_code=400, headers={"Cache-Control": "no-store"})


@router.post("/agent-gateway/oauth/token")
async def token(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").startswith("application/json"):
        data = {k: str(v) for k, v in (await request.json()).items()}
    else:
        data = {k: str(v) for k, v in (await request.form()).items()}
    kind = data.get("grant_type", "")
    now = datetime.now(UTC)
    with _db() as session:
        if kind == "authorization_code":
            grant = session.execute(select(OutsideOAuthGrant).where(OutsideOAuthGrant.token_hash == _hash(data.get("code", "")),
                                                                    OutsideOAuthGrant.kind == "code")).scalar_one_or_none()
            if grant is None or grant.used_at is not None or grant.expires_at < now or grant.client_id != data.get("client_id") \
                    or grant.redirect_uri != data.get("redirect_uri"):
                return _grant_error("invalid_grant", "The code is unknown, used, expired or for another connector.")
            verifier = data.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or challenge != grant.code_challenge:
                return _grant_error("invalid_grant", "The code verifier does not match.")
            grant.used_at = now
            agent = session.get(OutsideAgent, grant.agent_id)
            if agent is None or agent.status in ("revoked", "paused"):
                return _grant_error("invalid_grant", "The agent is not active.")
            body = _tokens(session, agent.id, grant.client_id)
        elif kind == "refresh_token":
            grant = session.execute(select(OutsideOAuthGrant).where(OutsideOAuthGrant.token_hash == _hash(data.get("refresh_token", "")),
                                                                    OutsideOAuthGrant.kind == "refresh")).scalar_one_or_none()
            if grant is None or grant.expires_at < now or (data.get("client_id") and grant.client_id != data.get("client_id")):
                return _grant_error("invalid_grant", "The refresh token is unknown or expired.")
            agent = session.get(OutsideAgent, grant.agent_id)
            if agent is None or agent.status in ("revoked", "paused"):
                return _grant_error("invalid_grant", "The agent is not active.")
            session.delete(grant)  # refresh tokens are used once
            body = _tokens(session, agent.id, grant.client_id)
        else:
            return _grant_error("unsupported_grant_type", "authorization_code or refresh_token.")
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def setup(base: str, credential: str) -> dict[str, str]:
    """How to connect, per client, with this credential (shown once with it)."""
    url = f"{base}/agent-gateway/mcp"
    return {
        "mcp_url": url,
        "claude_code": f'claude mcp add --transport http control-tower {url} --header "Authorization: Bearer {credential}"',
        "gemini_cli": json.dumps({"mcpServers": {"control-tower": {"httpUrl": url, "headers": {"Authorization": f"Bearer {credential}"}}}}, indent=2),
        "connectors": f"In Claude.ai or ChatGPT, add a custom connector with the address {url}; when it asks you to sign in, paste the credential.",
        "http": f'curl -H "Authorization: Bearer {credential}" {base}/agent-gateway/tools',
    }


def public_base() -> str:
    return (settings().public_base_url or "http://localhost:8000").rstrip("/")
