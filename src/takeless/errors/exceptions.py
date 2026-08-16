"""The exception hierarchy that produces consistent error responses.

Raise these from anywhere — a route, a service, a dependency — and the handlers
turn them into the same JSON shape every time. `code` is the machine-readable
half and is what clients should branch on; `message` is for humans and may
change without notice.
"""

from __future__ import annotations

from typing import Any


class TakelessError(Exception):
    """Base for everything this library raises."""


class AppError(TakelessError):
    """An error with an HTTP mapping, rendered as a structured response."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.status_code = status_code or type(self).status_code
        self.details = details
        self.headers = headers or {}
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r}, code={self.code!r})"


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    message = "The request could not be understood."


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource does not exist."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request payload is invalid."


class TooManyRequests(AppError):
    status_code = 429
    code = "too_many_requests"
    message = "Rate limit exceeded."


class InternalError(AppError):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "A dependency is unavailable."
