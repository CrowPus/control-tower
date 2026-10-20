"""Structured JSON logging with correlation context (spec §55).

Log records carry `request_id`, `correlation_id`, `agent_run_id`, `application_id`
and `user_id` when known. Secrets are scrubbed: any value that equals a configured
secret is replaced before the line is written, and well-known header names are
never logged at all.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

from ..config import settings

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("log_context", default=None)

_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|api[_-]?key|authorization|cookie|token|set-cookie)", re.IGNORECASE
)


def bind(**fields: Any) -> contextvars.Token[dict[str, Any] | None]:
    current = dict(_context.get() or {})
    current.update({k: v for k, v in fields.items() if v is not None})
    return _context.set(current)


def unbind(token: contextvars.Token[dict[str, Any] | None]) -> None:
    _context.reset(token)


def context() -> dict[str, Any]:
    return dict(_context.get() or {})


def _secret_values() -> list[str]:
    cfg = settings()
    values = [
        cfg.gemini_api_key,
        cfg.anthropic_api_key,
        cfg.openai_api_key,
        cfg.smtp_password,
        cfg.encryption_secret,
    ]
    return [v for v in values if v and len(v) >= 6]


def scrub(value: Any) -> Any:
    """Remove secret material from a log value, recursively."""
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if _SECRET_KEY_RE.search(str(k)) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        for secret in _secret_values():
            if secret in value:
                value = value.replace(secret, "[redacted]")
        return value
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        payload.update(scrub(context()))
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(scrub(extra))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = record.exc_info[0].__name__
            payload["exception_message"] = scrub(str(record.exc_info[1]))[:500]
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = context()
        suffix = " ".join(f"{k}={v}" for k, v in scrub(ctx).items()) if ctx else ""
        base = f"{record.levelname:7s} {record.name}: {scrub(record.getMessage())}"
        return f"{base} {suffix}".rstrip()


def configure_logging() -> None:
    cfg = settings()
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if cfg.log_json else PlainFormatter())
    root.addHandler(handler)
    root.setLevel(cfg.log_level.upper())
    # Uvicorn's access log duplicates the request middleware's line.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})
