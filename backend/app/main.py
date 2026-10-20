"""Control Tower's server: `uvicorn app.main:app`.

One process serves the admin (`/admin-api/…` and the built admin app), the gateway
for outside agents (`/agent-gateway/…` and its OAuth discovery), and the public
addresses of the studio's media. The admin's IP allowlist applies to everything
except the gateway and the media links, which outside services must reach.

In development the worker and scheduler run inside this process (EMBEDDED_WORKER);
in production they run as their own process (`python -m app.worker`).
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import public_media, runtime
from .admin.brain_routes import router as brain_router
from .admin.docs_routes import router as docs_router
from .admin.insights_routes import router as insights_router
from .admin.mail_routes import router as mail_router
from .admin.nia_browser_routes import router as nia_browser_router
from .admin.nia_routes import router as nia_router
from .admin.office_routes import router as office_router
from .admin.outside_routes import router as outside_router
from .admin.oversight_routes import router as oversight_router
from .admin.product_routes import router as product_router
from .admin.routes import router
from .admin.social_routes import router as social_router
from .admin.team_routes import router as team_router
from .config import settings
from .core import logging as logctx
from .core.errors import AppError
from .core.logging import configure_logging, get_logger, log_event
from .core.net import client_ip
from .frontend import mount_frontend
from .services.outside import gateway as outside_gateway

log = get_logger(__name__)

# Reachable from anywhere (outside agents, OAuth clients, the social networks fetching media): never behind the
# admin's IP allowlist. Each checks its own credential or token.
PUBLIC_PREFIXES = ("/agent-gateway/", "/.well-known/oauth-", public_media.PREFIX)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    cfg = settings()
    log_event(log, "control tower started", execution_mode=cfg.execution_mode)
    from .db import transaction
    from .services.insights.collector import COLLECTOR
    from .workers import (
        handlers as _handlers,  # noqa: F401 - registers every task handler, worker or not
    )

    COLLECTOR.start(lambda: transaction(write=True))
    if cfg.execution_mode != "test":
        runtime.start_embedded(worker=cfg.embedded_worker, scheduler=cfg.embedded_scheduler)
    try:
        yield
    finally:
        from .core.events import bus

        bus.close()
        runtime.stop_embedded()


def _error(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "retryable": False, "requires_user_action": False, **extra}}


def _allowed(ip: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(entry, strict=False) for entry in allowlist)


def create_app() -> FastAPI:
    cfg = settings()
    app = FastAPI(
        title=f"{cfg.app_name} API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None if cfg.is_production else "/admin-api/openapi.json",
    )
    allowlist = cfg.csv(cfg.admin_ip_allowlist)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = uuid.uuid4().hex[:16]
        token = logctx.bind(request_id=request_id, correlation_id=f"admin-{request_id}", path=request.url.path, method=request.method)
        started = time.perf_counter()
        try:
            if not request.url.path.startswith(PUBLIC_PREFIXES) and not _allowed(client_ip(request), allowlist):
                response: Response = JSONResponse(status_code=404, content=_error("NOT_FOUND", "Not found."))
            else:
                response = await call_next(request)
        except Exception:
            log.exception("unhandled admin error")
            response = JSONResponse(status_code=500, content=_error("INTERNAL_ERROR", "Something went wrong."))
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        # A mail's text is shown in a frame of the admin's own page, under its own stricter policy (admin/mail_routes.py).
        # So is an agent's proposed e-mail on its Approvals card (admin/team_routes.py).
        path = request.url.path
        mail_frame = ((path.startswith("/admin-api/mail/messages/") and path.endswith("/frame")) or
                      (path.startswith("/admin-api/team/approvals/") and path.endswith("/mail-preview"))) and "Content-Security-Policy" in response.headers
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if mail_frame else "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The microphone: talking to Nia (Admin → Nia), on the admin's own pages only.
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if not mail_frame:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        if cfg.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/admin-api/", "/agent-gateway/")):
            log_event(log, "request", status=response.status_code, duration_ms=int((time.perf_counter() - started) * 1000))
            from .services.insights.collector import COLLECTOR, app_of, route_template

            counted = app_of(request.url.path)
            if counted is not None:
                COLLECTOR.request(counted, route_template(request.scope), response.status_code, (time.perf_counter() - started) * 1000)
        logctx.unbind(token)
        return response

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.after_rollback:
            try:
                await asyncio.to_thread(exc.run_after_rollback)
            except Exception:  # noqa: BLE001 - never turn a refusal into a crash
                log.exception("could not record a refused attempt")
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_payload("")})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error("NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]} for err in exc.errors() if err.get("loc")]
        # Which field and why, never what was typed: enough to see what went wrong for the admin.
        log.info("admin validation failed: %s %s", _request.url.path, "; ".join(f"{f['field']}: {f['message']}" for f in fields)[:500])
        return JSONResponse(status_code=422, content=_error("VALIDATION_FAILED", "Please check these fields." if fields else "Check the highlighted fields.", fields=fields))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.error("admin database error: %s", exc.__class__.__name__)
        return JSONResponse(status_code=500, content=_error("DATABASE_ERROR", "Database operation failed."))

    @app.get("/admin-api/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for each in (router, insights_router, team_router, nia_router, brain_router, office_router, mail_router, social_router,
                 product_router, oversight_router, nia_browser_router, docs_router, outside_router,
                 outside_gateway.router, public_media.router):
        app.include_router(each)
    mount_frontend(app, cfg.admin_frontend_dist, api_prefix="admin-api")
    return app


app = create_app()
