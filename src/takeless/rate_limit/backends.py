"""Rate limit counters, in memory or in Redis.

Both implement a fixed window: the first request in a window starts a counter
that expires with it. It over-admits at a window boundary compared with a
sliding window, and in exchange costs one round trip and no per-request state —
the right trade for protecting a service rather than metering billing.

Memory is per-process, so N replicas mean N times the limit. Use Redis the
moment you run more than one.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

from takeless.core.deps import require_dependency
from takeless.rate_limit.rules import Rule, Verdict

if TYPE_CHECKING:
    from takeless.core.component import Check


class RateLimitBackend(Protocol):
    """Where the counters live."""

    async def hit(self, key: str, rule: Rule) -> Verdict:
        """Record one request against `key` and say whether it is allowed."""
        ...

    async def reset(self, key: str) -> None:
        """Forget `key`'s counter."""
        ...

    async def close(self) -> None: ...

    async def check(self) -> Check | None: ...


class MemoryBackend:
    """Process-local counters. Fine for a single replica, tests and local runs."""

    #: Expired entries are only dropped when a hit happens, so a burst of
    #: one-off keys would otherwise grow the dict forever. Sweep every N hits.
    _SWEEP_EVERY = 1024

    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, float]] = {}
        self._since_sweep = 0

    async def hit(self, key: str, rule: Rule) -> Verdict:
        now = time.monotonic()
        count, expires_at = self._counters.get(key, (0, 0.0))
        if expires_at <= now:
            count, expires_at = 0, now + rule.window_seconds

        count += 1
        self._counters[key] = (count, expires_at)

        self._since_sweep += 1
        if self._since_sweep >= self._SWEEP_EVERY:
            self._sweep(now)

        return Verdict(
            allowed=count <= rule.limit,
            limit=rule.limit,
            remaining=rule.limit - count,
            reset_after=expires_at - now,
        )

    async def reset(self, key: str) -> None:
        self._counters.pop(key, None)

    async def close(self) -> None:
        self._counters.clear()

    async def check(self) -> Check | None:
        return None

    def _sweep(self, now: float) -> None:
        self._since_sweep = 0
        expired = [key for key, (_, at) in self._counters.items() if at <= now]
        for key in expired:
            del self._counters[key]


#: INCR the counter, and set the window's TTL only on the request that created
#: it — so the window is fixed from the first hit, not extended by every hit.
_HIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('PTTL', KEYS[1])}
"""


class RedisBackend:
    """Shared counters, so a limit means the same thing across replicas.

    Requires `pip install 'takeless[redis]'`.
    """

    def __init__(self, url: str, **client_options: Any) -> None:
        redis_asyncio = require_dependency("redis.asyncio")
        self.client: Any = redis_asyncio.from_url(
            url, decode_responses=True, **client_options
        )
        self._script: Any = self.client.register_script(_HIT_SCRIPT)

    async def hit(self, key: str, rule: Rule) -> Verdict:
        count, ttl_ms = await self._script(
            keys=[key], args=[rule.window_seconds * 1000]
        )
        count = int(count)
        return Verdict(
            allowed=count <= rule.limit,
            limit=rule.limit,
            remaining=rule.limit - count,
            reset_after=max(int(ttl_ms), 0) / 1000,
        )

    async def reset(self, key: str) -> None:
        await self.client.delete(key)

    async def close(self) -> None:
        await self.client.aclose()

    async def check(self) -> Check | None:
        from takeless.core.component import Check

        await self.client.ping()
        return Check(name="rate_limit", healthy=True, meta={"backend": "redis"})
