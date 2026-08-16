"""Per-request context, carried in contextvars.

Anything bound here lands on every log line emitted for the rest of the
request, and is readable by modules that need it without being handed it —
`errors` stamps the request id onto error responses this way.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import structlog

_request_id: ContextVar[str | None] = ContextVar("takeless_request_id", default=None)


def get_request_id() -> str | None:
    """The current request's id, or `None` outside a request."""
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    """Set the current request's id and bind it onto every later log line."""
    _request_id.set(request_id)
    structlog.contextvars.bind_contextvars(request_id=request_id)


def bind_context(**values: Any) -> None:
    """Add fields to every log line for the rest of this request.

    `require_auth` uses this to bind `user_id`, which is why the brief's
    `logger.info("user_fetched_profile")` comes out with a user on it without
    the endpoint passing one.
    """
    structlog.contextvars.bind_contextvars(**values)


def unbind_context(*keys: str) -> None:
    """Remove fields previously bound with `bind_context`."""
    structlog.contextvars.unbind_contextvars(*keys)


def get_context() -> dict[str, Any]:
    """Everything currently bound, as a plain dict."""
    return dict(structlog.contextvars.get_contextvars())


def clear_context() -> None:
    """Drop all bound context. The request middleware calls this on entry, so a
    recycled worker task never inherits the previous request's fields."""
    _request_id.set(None)
    structlog.contextvars.clear_contextvars()
