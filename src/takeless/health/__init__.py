"""Health checks, aggregated over whatever modules are configured.

You do not list the things to probe. Every component that can be probed
implements `check()`, so configuring a database is what puts the database in
`/health` — there is no second place to keep in sync.

Two endpoints, because orchestrators want two different questions answered:
`/health/live` says the process is up (never probes dependencies, so a flapping
database cannot get your pods restarted), and `/health` says the service can
actually serve traffic.

Standalone:

    from takeless.health import Health

    Health().setup(app)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from takeless.core.component import Check, Component, components_of
from takeless.observability.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["Health", "HealthConfig"]

_logger = get_logger("takeless.health")

#: Components with nothing to probe; excluded so they never appear as checks.
_NOT_PROBED = frozenset(
    {"takeless", "health", "errors", "cors", "docs", "observability"}
)


class HealthConfig(BaseModel):
    """Where the probes live and how much they say."""

    model_config = ConfigDict(extra="forbid")

    path: str = "/health"
    liveness_path: str = "/health/live"

    #: Per-component results in the body. Turn off on a public endpoint — the
    #: check names describe your infrastructure.
    include_details: bool = True

    #: Health endpoints in the OpenAPI schema. Off: they are for your
    #: orchestrator, not for API consumers.
    include_in_schema: bool = False

    #: Ceiling on each individual probe. A hung connection pool must not hold
    #: the endpoint open until the orchestrator's own timeout fires.
    timeout: float = 5.0

    unhealthy_status_code: int = 503


class Health(Component):
    """Serves the liveness and readiness endpoints."""

    name = "health"

    def __init__(self, config: HealthConfig | None = None) -> None:
        self.config = config or HealthConfig()

    def setup(self, app: FastAPI) -> None:
        super().setup(app)
        config = self.config

        async def liveness() -> dict[str, str]:
            return {"status": "ok"}

        async def readiness(request: Request) -> JSONResponse:
            checks = await self.run_checks(request.app)
            healthy = all(check.healthy for check in checks.values())
            body: dict[str, Any] = {"status": "ok" if healthy else "unhealthy"}
            if config.include_details:
                body["checks"] = {
                    name: _serialise(check) for name, check in checks.items()
                }
            if not healthy:
                _logger.warning(
                    "health_check_failed",
                    failing=[n for n, c in checks.items() if not c.healthy],
                )
            return JSONResponse(
                body,
                status_code=200 if healthy else config.unhealthy_status_code,
            )

        app.get(config.liveness_path, include_in_schema=config.include_in_schema)(
            liveness
        )
        app.get(config.path, include_in_schema=config.include_in_schema)(readiness)

    async def run_checks(self, app: FastAPI) -> dict[str, Check]:
        """Probe every probeable component on `app`, concurrently."""
        probeable = [
            component
            for name, component in components_of(app).items()
            if name not in _NOT_PROBED
        ]
        results = await asyncio.gather(
            *(self._probe(component) for component in probeable)
        )
        return {check.name: check for check in results if check is not None}

    async def _probe(self, component: Component) -> Check | None:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.config.timeout):
                check = await component.check()
        except TimeoutError:
            return Check(
                name=component.name,
                healthy=False,
                detail=f"probe timed out after {self.config.timeout}s",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            return Check(
                name=component.name,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        if check is None:
            return None
        if check.latency_ms is None:
            check = Check(
                name=check.name,
                healthy=check.healthy,
                detail=check.detail,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                meta=check.meta,
            )
        return check


def _serialise(check: Check) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "ok" if check.healthy else "unhealthy"}
    if check.latency_ms is not None:
        body["latency_ms"] = check.latency_ms
    if check.detail:
        body["detail"] = check.detail
    body.update(check.meta)
    return body
