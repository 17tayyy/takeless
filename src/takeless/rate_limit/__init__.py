"""Rate limiting, globally or per route.

Two ways in, sharing one counter store: a global default applied by middleware,
and a per-route dependency for the endpoints that need something tighter than
the default (login, password reset, anything that sends email).

    from takeless.rate_limit import RateLimit, RateLimiter, RateLimitConfig

    RateLimiter(RateLimitConfig(default_limit="100/minute")).setup(app)

    @app.post("/login", dependencies=[Depends(RateLimit("5/minute"))])
    async def login(): ...

The memory backend counts per process. With more than one replica that means
each of them allows the full limit — point it at Redis and the limit becomes
the number you actually wrote.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, model_validator

from takeless.core.component import Check, Component, get_component
from takeless.errors.exceptions import TooManyRequests
from takeless.rate_limit.backends import MemoryBackend, RateLimitBackend, RedisBackend
from takeless.rate_limit.middleware import RateLimitMiddleware
from takeless.rate_limit.rules import RateLimitConfigError, Rule, Verdict

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "MemoryBackend",
    "RateLimit",
    "RateLimitBackend",
    "RateLimitConfig",
    "RateLimitConfigError",
    "RateLimiter",
    "RedisBackend",
    "Rule",
    "Verdict",
    "client_key",
]


def client_key(request: Request, *, trust_forwarded: bool = False) -> str:
    """Who is being limited: the caller's address.

    `X-Forwarded-For` is ignored unless you opt in, because a header the client
    controls is a header the client can rotate — trusting it without a proxy in
    front turns the limit off.
    """
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "anonymous"


class RateLimitConfig(BaseModel):
    """Backend, default limit, and how requests are bucketed."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    backend: Literal["memory", "redis"] = "memory"

    #: Redis URL. Required when `backend="redis"`.
    url: str | None = None

    #: Applied to every request by the middleware. `None` installs no
    #: middleware at all, leaving only the per-route dependencies.
    default_limit: str | None = None

    exclude_paths: tuple[str, ...] = ("/health", "/health/live", "/health/ready")

    #: Read the caller's address from `X-Forwarded-For`. Only turn on when a
    #: proxy you control sets it.
    trust_forwarded: bool = False

    #: Give each path its own bucket, so a busy endpoint does not spend another
    #: endpoint's allowance.
    per_path: bool = True

    #: Emit `X-RateLimit-*` on every response, not just refusals.
    send_headers: bool = True

    #: Namespace for the keys, so several services can share one Redis.
    prefix: str = "takeless:rl"

    #: Override how a caller is identified — by API key or user id, say.
    key_func: Callable[[Request], str] | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> RateLimitConfig:
        if self.backend == "redis" and not self.url:
            raise ValueError("rate_limit: backend='redis' needs url='redis://...'")
        if self.default_limit is not None:
            Rule.parse(self.default_limit)
        return self


class RateLimiter(Component):
    """Owns the counter backend and the global limit."""

    name = "rate_limit"

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self.backend: RateLimitBackend = (
            RedisBackend(self.config.url or "")
            if self.config.backend == "redis"
            else MemoryBackend()
        )
        self.default_rule = (
            Rule.parse(self.config.default_limit) if self.config.default_limit else None
        )

    def setup(self, app: FastAPI) -> None:
        super().setup(app)
        if self.default_rule is not None:
            app.add_middleware(RateLimitMiddleware, limiter=self)

    async def shutdown(self) -> None:
        await self.backend.close()

    async def check(self) -> Check | None:
        return await self.backend.check()

    def global_bucket(self, request: Request) -> str:
        """The bucket the middleware's default limit counts into."""
        if not self.config.per_path:
            return "global"
        return f"global:{request.scope.get('path', '')}"

    def route_bucket(self, request: Request, scope: str | None) -> str:
        """The bucket one route's own limit counts into.

        Kept separate from `global_bucket` even when the two rules match: a
        route that also has the default applied to it must not have its request
        counted twice and be refused at half its stated limit.
        """
        if scope is not None:
            return scope
        return f"route:{request.scope.get('path', '')}"

    def key_for(self, request: Request, rule: Rule, bucket: str) -> str:
        """The counter key for this request, in this bucket, under this rule."""
        if self.config.key_func is not None:
            identity = self.config.key_func(request)
        else:
            identity = client_key(request, trust_forwarded=self.config.trust_forwarded)

        return _digest([self.config.prefix, bucket, identity, str(rule)])

    async def hit(self, request: Request, rule: Rule, *, bucket: str) -> Verdict:
        """Count this request and return the verdict, without raising."""
        return await self.backend.hit(self.key_for(request, rule, bucket), rule)

    async def enforce(self, request: Request, rule: Rule, *, bucket: str) -> Verdict:
        """Count this request and raise `TooManyRequests` if it is over."""
        verdict = await self.hit(request, rule, bucket=bucket)
        if not verdict.allowed:
            raise TooManyRequests(
                f"Rate limit of {rule.limit} requests per "
                f"{rule.window_seconds}s exceeded.",
                headers=verdict.headers(),
            )
        return verdict


def RateLimit(
    limit: str | Rule, *, scope: str | None = None
) -> Callable[[Request], Any]:
    """A dependency enforcing `limit` on one route.

        @app.post("/login", dependencies=[Depends(RateLimit("5/minute"))])

    `scope` shares one bucket across several routes — give the same scope to
    every endpoint that sends an email, and they draw from one allowance.
    """
    rule = Rule.parse(limit)

    async def dependency(request: Request) -> Verdict:
        limiter = get_component(request.app, RateLimiter)
        bucket = limiter.route_bucket(request, scope)
        verdict = await limiter.enforce(request, rule, bucket=bucket)
        if limiter.config.send_headers:
            request.scope.setdefault("takeless_rate_limit", verdict.headers())
        return verdict

    return dependency


def _digest(parts: list[str]) -> str:
    """A bounded-length key. Paths and identities are attacker-influenced, and
    unbounded Redis keys are their own problem."""
    joined = "|".join(parts)
    if len(joined) <= 200:
        return joined
    return f"{parts[0]}:{hashlib.blake2b(joined.encode(), digest_size=16).hexdigest()}"
