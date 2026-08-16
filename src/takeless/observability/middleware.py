"""Request context middleware.

Deliberately a raw ASGI middleware rather than `BaseHTTPMiddleware`: the latter
runs the endpoint in a child task, so contextvars bound *inside* a dependency
(`user_id`, say) never make it back out to the access log line. Running in the
same task keeps the whole request on one context.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from takeless.observability.context import (
    clear_context,
    get_context,
    set_request_id,
)
from takeless.observability.logging import get_logger

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from takeless.observability.config import ObservabilityConfig


class RequestContextMiddleware:
    """Binds a request id (and the request's shape) onto the log context, adds
    the id to the response headers, and logs one line per finished request."""

    def __init__(self, app: ASGIApp, config: ObservabilityConfig) -> None:
        self.app = app
        self.config = config
        self._header = config.request_id_header.lower().encode()
        self._logger = get_logger("takeless.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        clear_context()
        request_id = self._request_id(scope)
        set_request_id(request_id)

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((self._header, request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._log(method, path, status, started)
            raise
        else:
            self._log(method, path, status, started)
        finally:
            clear_context()

    def _request_id(self, scope: Scope) -> str:
        if self.config.trust_incoming_request_id:
            for key, value in scope.get("headers", ()):
                if key == self._header and value:
                    return value.decode("latin-1")[:200]
        return uuid.uuid4().hex

    def _log(self, method: str, path: str, status: int, started: float) -> None:
        if not self.config.access_log or path in self.config.exclude_paths:
            return
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        self._logger.info(
            "request",
            http_method=method,
            path=path,
            status=status,
            duration_ms=duration_ms,
            **{k: v for k, v in get_context().items() if k != "request_id"},
        )
