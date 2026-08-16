"""Global rate limiting.

Raw ASGI rather than `BaseHTTPMiddleware` for the same reason as the request
context middleware: it shares the request's context, so the refusal it writes
still carries the request id. Because middleware runs outside Starlette's
exception middleware, the 429 is built here directly instead of being raised —
a raise at this level would escape the handlers and become a 500.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from takeless.errors.config import ErrorsConfig
from takeless.errors.exceptions import TooManyRequests
from takeless.errors.handlers import build_response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from takeless.rate_limit import RateLimiter

#: Where a per-route dependency leaves its headers for us to attach.
SCOPE_HEADERS_KEY = "takeless_rate_limit"


class RateLimitMiddleware:
    """Applies the configured default limit to every request."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        rule = self.limiter.default_rule
        if scope["type"] != "http" or rule is None:
            await self.app(scope, receive, send)
            return

        config = self.limiter.config
        if scope.get("path", "") in config.exclude_paths:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        verdict = await self.limiter.hit(
            request, rule, bucket=self.limiter.global_bucket(request)
        )

        if not verdict.allowed:
            refusal = TooManyRequests(
                f"Rate limit of {rule.limit} requests per "
                f"{rule.window_seconds}s exceeded.",
                headers=verdict.headers(),
            )
            response = build_response(
                _errors_config(scope),
                status_code=refusal.status_code,
                code=refusal.code,
                message=refusal.message,
                headers=refusal.headers,
            )
            await response(scope, receive, send)
            return

        headers = verdict.headers() if config.send_headers else {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                final = {**headers, **scope.get(SCOPE_HEADERS_KEY, {})}
                raw = message.setdefault("headers", [])
                for key, value in final.items():
                    raw.append((key.lower().encode(), value.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _errors_config(scope: Scope) -> ErrorsConfig:
    """The app's error envelope settings, or the defaults if `errors` is off."""
    app = scope.get("app")
    components = getattr(getattr(app, "state", None), "takeless_components", None) or {}
    errors = components.get("errors")
    return getattr(errors, "config", None) or ErrorsConfig()
