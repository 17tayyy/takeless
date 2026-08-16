"""Parsing for limit expressions like `100/minute` or `30/15minutes`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from takeless.errors.exceptions import TakelessError

_PERIODS = {
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "s": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "m": 60,
    "hour": 3600,
    "hours": 3600,
    "h": 3600,
    "day": 86400,
    "days": 86400,
    "d": 86400,
}

_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*([a-zA-Z]+)\s*$")


class RateLimitConfigError(TakelessError, ValueError):
    """A limit expression could not be parsed.

    Also a `ValueError` so that pydantic folds it into the surrounding
    `ValidationError` — a bad limit then reports which field it came from.
    """


@dataclass(slots=True, frozen=True)
class Rule:
    """`limit` requests allowed per `window_seconds`."""

    limit: int
    window_seconds: int

    @classmethod
    def parse(cls, expression: str | Rule) -> Rule:
        """`Rule.parse("100/minute")`, `Rule.parse("30/15minutes")`."""
        if isinstance(expression, Rule):
            return expression
        match = _PATTERN.match(expression)
        if match is None:
            raise RateLimitConfigError(
                f"cannot parse rate limit {expression!r}; expected something like "
                f"'100/minute' or '30/15minutes'"
            )
        count, multiplier, period = match.groups()
        unit = _PERIODS.get(period.lower())
        if unit is None:
            raise RateLimitConfigError(
                f"unknown period {period!r} in {expression!r}; use second, minute, "
                f"hour or day"
            )
        window = int(multiplier or 1) * unit
        if int(count) < 1 or window < 1:
            raise RateLimitConfigError(f"rate limit {expression!r} allows nothing")
        return cls(limit=int(count), window_seconds=window)

    def __str__(self) -> str:
        return f"{self.limit}/{self.window_seconds}s"


@dataclass(slots=True, frozen=True)
class Verdict:
    """What the backend decided about one request."""

    allowed: bool
    limit: int
    remaining: int
    #: Seconds until the current window resets.
    reset_after: float

    def headers(self) -> dict[str, str]:
        """The `X-RateLimit-*` set, plus `Retry-After` when refused."""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(self.remaining, 0)),
            "X-RateLimit-Reset": str(int(self.reset_after) + 1),
        }
        if not self.allowed:
            headers["Retry-After"] = str(int(self.reset_after) + 1)
        return headers
