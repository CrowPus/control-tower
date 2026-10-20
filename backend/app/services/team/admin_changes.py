"""Nia changes things in the admin (Admin → Docs "Nia"): she proposes one exact admin request;
it waits in Approvals as the owner's decision ("Always you"), and when the owner approves it on
screen, the owner's own admin page sends it with the owner's own sign-in. The server never acts
as anyone: it checks the request before it is shown and again before the browser sends it, and
records what came back.

Some parts of the admin are never Nia's, also not with an approval: sign-in, the admin accounts,
authenticator recovery, and deciding approvals (she cannot approve herself)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

ACTION = "admin.change"
CHANGE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
BLOCKED = (
    re.compile(r"^/admin-api/auth(/|$)"),
    re.compile(r"^/admin-api/admins(/|$)"),
    re.compile(r"^/admin-api/employers/[^/]+/users/[^/]+/mfa-recovery$"),
    re.compile(r"^/admin-api/team/approvals(/|$)"),
    re.compile(r"^/admin-api/team/agents/[^/]+/offers(/|$)"),
    re.compile(r"^/admin-api/nia/voice(/|$)"),
    # An outside agent's credential is shown once to the owner; Nia never issues one (nor changes an outside agent's access).
    re.compile(r"^/admin-api/outside(/|$)"),
)


class Refused(ValueError):
    """Not a change Nia may propose; the reason is shown."""


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    pattern: re.Pattern[str]
    what: str


def _admin_app() -> Any:
    from ...main import app

    return app


@cache
def endpoints() -> tuple[Endpoint, ...]:
    """Every admin function, from the admin's own API description (the app's route list nests routers)."""
    found = []
    for path, operations in _admin_app().openapi().get("paths", {}).items():
        if not path.startswith("/admin-api/"):
            continue
        pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
        for method, operation in operations.items():
            if method.upper() in ("GET", *CHANGE_METHODS):
                what = (operation.get("description") or operation.get("summary") or "").strip().splitlines()
                found.append(Endpoint(method.upper(), path, pattern, what[0] if what else ""))
    return tuple(found)


def blocked(path: str) -> bool:
    return any(b.match(path) for b in BLOCKED)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    """The request in its clean form, or why it is refused."""
    method = str(payload.get("method", "")).upper().strip()
    path = str(payload.get("path", "")).split("?")[0].strip()
    if method not in CHANGE_METHODS:
        raise Refused("A change is POST, PUT, PATCH or DELETE.")
    if not path.startswith("/admin-api/") or ".." in path or "//" in path or not re.fullmatch(r"[A-Za-z0-9/_.\-]+", path):
        raise Refused("Give an admin address that starts with /admin-api/.")
    if blocked(path):
        raise Refused("This part of the admin is never Nia's: sign-in, admin accounts, authenticator recovery and deciding approvals stay with the owner.")
    if not any(e.method == method and e.pattern.match(path) for e in endpoints()):
        raise Refused(f"There is no {method} {path} in the admin. Look it up with admin_endpoints.")
    query = payload.get("query") or {}
    body = payload.get("body")
    if not isinstance(query, dict) or not all(isinstance(v, (str, int, float, bool)) for v in query.values()):
        raise Refused("query is an object of simple values.")
    if body is not None and not isinstance(body, (dict, list)):
        raise Refused("body is a JSON object (or list).")
    _check_body(method, path, body)
    return {"method": method, "path": path, "query": {str(k): v for k, v in query.items()}, "body": body}


def _routes(routes: Any, prefix: str = "") -> Any:
    """Every route of the admin app (included routers are kept nested in this FastAPI version)."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix, route
        elif hasattr(route, "original_router"):
            extra = str(getattr(getattr(route, "include_context", None), "prefix", "") or "")
            yield from _routes(route.original_router.routes, prefix + extra)


def _body_type(method: str, path: str) -> Any:
    """The type the admin function reads its body as (None when it takes none)."""
    for prefix, route in _routes(_admin_app().routes):
        if method in route.methods and re.fullmatch("^" + re.sub(r"\{[^}]+\}", "[^/]+", prefix + route.path) + "$", path):
            field = route.body_field
            return getattr(getattr(field, "field_info", None), "annotation", None) if field is not None else None
    return None


def _check_body(method: str, path: str, body: Any) -> None:
    """The body is what the admin function expects — checked before the owner ever sees the card (2026-10-09: four of Nia's changes
    failed when the owner approved them). A PUT replaces the whole setting: every part must be in it, or the parts left out would
    be reset (one of those changes would have wiped the owner's monthly budget)."""
    from pydantic import BaseModel, TypeAdapter, ValidationError

    kind = _body_type(method, path)
    if kind is None:
        return
    if body is None:
        raise Refused("This admin function needs a body (see admin_endpoints).")
    try:
        TypeAdapter(kind).validate_python(body)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(x) for x in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()[:6])
        raise Refused(f"The admin would refuse this body — {problems}. Fix it (admin_endpoints shows the shape) and propose again.") from exc
    if method == "PUT" and isinstance(kind, type) and issubclass(kind, BaseModel) and isinstance(body, dict):
        missing = [name for name in kind.model_fields if name not in body]
        if missing:
            raise Refused("A PUT replaces the whole setting, so every part must be in the body with its current value, or it is reset: "
                          f"missing {', '.join(missing)}. Read the current values first (the page, or ask the owner), then send the whole object.")


def _schema(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "…"
    if isinstance(node, dict):
        if "$ref" in node:
            return _schema(spec, spec.get("components", {}).get("schemas", {}).get(node["$ref"].rsplit("/", 1)[-1], {}), depth + 1)
        keep = ("type", "items", "enum", "required", "anyOf", "maxLength", "minimum", "maximum", "default", "description")
        out = {k: _schema(spec, v, depth + 1) for k, v in node.items() if k in keep}
        if isinstance(node.get("properties"), dict):
            out["properties"] = {name: _schema(spec, v, depth + 1) for name, v in node["properties"].items()}
        return out
    if isinstance(node, list):
        return [_schema(spec, v, depth + 1) for v in node]
    return node


def describe(prefixes: list[str]) -> list[dict[str, Any]]:
    """The admin functions under these addresses and what each takes (from the admin's API description)."""
    spec = _admin_app().openapi()
    out = []
    for e in endpoints():
        if blocked(e.path) or (prefixes and not any(e.path == p or e.path.startswith(p.rstrip("/") + "/") for p in prefixes)):
            continue
        operation = spec.get("paths", {}).get(e.path, {}).get(e.method.lower(), {})
        item: dict[str, Any] = {"method": e.method, "path": e.path, "what": e.what, "change": e.method in CHANGE_METHODS}
        query = [p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"]
        if query:
            item["query"] = query
        body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if body:
            item["body"] = _schema(spec, body)
        out.append(item)
    return out
