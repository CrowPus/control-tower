"""Typed application errors (spec §72).

Every error that can reach a user or a worker carries a stable machine code, a
human message, whether a retry could help, and whether a person has to act. The
HTTP layer renders these as JSON; the worker uses `retryable` to decide between
back-off and the dead-letter queue.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "APP_ERROR"
    retryable = False
    requires_user_action = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        requires_user_action: bool | None = None,
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        if requires_user_action is not None:
            self.requires_user_action = requires_user_action
        self.entity_type = entity_type
        self.entity_id = str(entity_id) if entity_id is not None else None
        self.details = details or {}
        # Work that must survive the rollback this error causes (a failed sign-in
        # attempt must still count). The error handlers run it after the
        # request's transaction has ended, in a transaction of its own.
        self.after_rollback: list[Any] = []

    def run_after_rollback(self) -> None:
        for action in self.after_rollback:
            action()

    def to_payload(self, correlation_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "requires_user_action": self.requires_user_action,
        }
        if self.entity_type:
            body["entity_type"] = self.entity_type
        if self.entity_id:
            body["entity_id"] = self.entity_id
        if self.details:
            body["details"] = self.details
        if correlation_id:
            body["correlation_id"] = correlation_id
        return body


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


class AIConsentRequired(Forbidden):
    """The member has not agreed that their data goes to Google (Gemini) for the AI features."""

    code = "AI_CONSENT_REQUIRED"


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class AutomationPaused(AppError):
    status_code = 409
    code = "AUTOMATION_PAUSED"
    retryable = True


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    retryable = True


class TransientExternalError(AppError):
    """Network timeouts, 5xx, temporary throttling — safe to retry with back-off."""

    status_code = 502
    code = "EXTERNAL_TRANSIENT"
    retryable = True


class PermanentExternalError(AppError):
    status_code = 502
    code = "EXTERNAL_PERMANENT"


class IntegrationNotConfigured(AppError):
    status_code = 409
    code = "INTEGRATION_NOT_CONFIGURED"
    requires_user_action = True


class AIUnavailable(AppError):
    status_code = 503
    code = "AI_UNAVAILABLE"
    retryable = True


class AIStructuredOutputInvalid(AppError):
    status_code = 502
    code = "AI_OUTPUT_INVALID"
    retryable = True


class BudgetExceeded(AppError):
    status_code = 429
    code = "AI_BUDGET_EXCEEDED"
    requires_user_action = True
