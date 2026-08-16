"""`Takeless` — the fast path.

This object owns no behaviour. It coerces each keyword into that module's
config, instantiates the module, and calls `setup` / `startup` / `shutdown` /
`check` on it. Everything it can do, you can do by hand with the modules
directly; it exists so that the common case is one object instead of ten.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from takeless.core.component import Component, register

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from structlog.stdlib import BoundLogger

    from takeless.auth import Auth
    from takeless.db import Database
    from takeless.docs import Docs
    from takeless.errors import Errors
    from takeless.health import Health
    from takeless.jobs import Jobs
    from takeless.observability import Observability
    from takeless.rate_limit import RateLimiter
    from takeless.settings import BaseAppSettings

#: Setup runs in this order. Logging first so every later step can log; errors
#: before the routes they protect; docs last so it sees the finished OpenAPI.
_SETUP_ORDER = (
    "observability",
    "errors",
    "secrets",
    "db",
    "jobs",
    "auth",
    "rate_limit",
    "cors",
    "health",
    "docs",
)

#: Middleware is added in reverse of the order it should run, because Starlette
#: treats the last-added as outermost. Final order: CORS, then request context,
#: then rate limiting — so a 429 still carries a request id and CORS headers.
_MIDDLEWARE_ORDER = ("rate_limit", "observability", "cors")


class Takeless:
    """Every configured module, wired into one FastAPI app by `setup()`.

    A module is off unless you configure it, except the four that need no
    configuration to be useful: logging, error handling, health and docs. Pass
    `False` to turn one of those off.

    Each keyword takes that module's config object, a dict of its fields, or
    `True` for defaults.
    """

    def __init__(
        self,
        *,
        settings: BaseAppSettings | None = None,
        logging: Any = None,
        errors: Any = None,
        health: Any = None,
        docs: Any = None,
        auth: Any = None,
        jobs: Any = None,
        db: Any = None,
        rate_limit: Any = None,
        cors: Any = None,
        secrets: Any = None,
    ) -> None:
        self.settings = settings
        self._components: dict[str, Component] = {}
        self._started: list[Component] = []
        self._apps: set[int] = set()

        environment = getattr(settings, "environment", "development")
        service = getattr(settings, "app_name", None)

        self.observability = self._build_observability(logging, environment, service)
        self.secrets = self._build_secrets(secrets)
        self.errors = self._build_errors(errors)
        self.health = self._build_health(health)
        self.docs = self._build_docs(docs, environment)
        self.auth = self._build_auth(auth)
        self.jobs = self._build_jobs(jobs)
        self.db = self._build_db(db)
        self.rate_limit = self._build_rate_limit(rate_limit)
        self.cors = self._build_cors(cors)

    def _keep(self, component: Component | None) -> Any:
        if component is not None:
            self._components[component.name] = component
        return component

    def _build_observability(
        self, value: Any, environment: str, service: str | None
    ) -> Observability | None:
        from takeless.observability import Observability, ObservabilityConfig

        config = _coerce(value, ObservabilityConfig, default_on=True)
        if config is None:
            return None
        if config.json_logs is None:
            config = config.model_copy(
                update={"json_logs": environment != "development"}
            )
        if config.service is None:
            config = config.model_copy(update={"service": service})
        return self._keep(Observability(config))

    def _build_secrets(self, value: Any) -> Any:
        if value is None or value is False:
            return None
        from takeless.settings import Secrets, SecretsConfig

        config = _coerce(value, SecretsConfig)
        return self._keep(Secrets(config)) if config else None

    def _build_errors(self, value: Any) -> Errors | None:
        from takeless.errors import Errors, ErrorsConfig

        config = _coerce(value, ErrorsConfig, default_on=True)
        return self._keep(Errors(config)) if config else None

    def _build_health(self, value: Any) -> Health | None:
        from takeless.health import Health, HealthConfig

        config = _coerce(value, HealthConfig, default_on=True)
        return self._keep(Health(config)) if config else None

    def _build_docs(self, value: Any, environment: str) -> Docs | None:
        from takeless.docs import Docs, DocsConfig

        config = _coerce(value, DocsConfig, default_on=True)
        if config is None:
            return None
        if config.environment is None:
            config = config.model_copy(update={"environment": environment})
        return self._keep(Docs(config))

    def _build_auth(self, value: Any) -> Auth | None:
        if value is None or value is False:
            return None
        from takeless.auth import Auth, AuthConfig

        config = _coerce(value, AuthConfig)
        return self._keep(Auth(config)) if config else None

    def _build_jobs(self, value: Any) -> Jobs | None:
        if value is None or value is False:
            return None
        from takeless.jobs import Jobs, JobsConfig

        config = _coerce(value, JobsConfig)
        return self._keep(Jobs(config)) if config else None

    def _build_db(self, value: Any) -> Database | None:
        if value is None or value is False:
            return None
        from takeless.db import Database, DatabaseConfig

        config = _coerce(value, DatabaseConfig)
        return self._keep(Database(config)) if config else None

    def _build_rate_limit(self, value: Any) -> RateLimiter | None:
        if value is None or value is False:
            return None
        from takeless.rate_limit import RateLimitConfig, RateLimiter

        config = _coerce(value, RateLimitConfig)
        return self._keep(RateLimiter(config)) if config else None

    def _build_cors(self, value: Any) -> Any:
        if value is None or value is False:
            return None
        from takeless.cors import Cors, CorsConfig

        config = _coerce(value, CorsConfig)
        return self._keep(Cors(config)) if config else None

    @property
    def logger(self) -> BoundLogger:
        """The configured structured logger, already carrying request context."""
        from takeless.observability import get_logger

        return get_logger(getattr(self.settings, "app_name", "takeless"))

    @property
    def components(self) -> dict[str, Component]:
        """Every enabled module, keyed by name."""
        return dict(self._components)

    def setup(self, app: FastAPI) -> None:
        """Hook every enabled module into `app`.

        Safe to call once per app; calling it twice on the same app raises
        rather than stacking a second copy of every middleware.
        """
        if id(app) in self._apps:
            raise RuntimeError("takeless.setup() was already called on this app")
        self._apps.add(id(app))

        register(app, _TakelessHandle(self))  # so /health can reach the set
        app.state.takeless = self

        for name in _SETUP_ORDER:
            component = self._components.get(name)
            if component is not None and name not in _MIDDLEWARE_ORDER:
                component.setup(app)

        for name in _MIDDLEWARE_ORDER:
            component = self._components.get(name)
            if component is not None:
                component.setup(app)

        self._attach_lifespan(app)

    def _attach_lifespan(self, app: FastAPI) -> None:
        """Wrap the app's lifespan so components open and close around it.

        Wrapping rather than assigning keeps a user-supplied `lifespan=` intact
        — theirs runs inside ours, with connections already open.
        """
        previous = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
            await self.startup()
            try:
                async with previous(app) as state:
                    yield state
            finally:
                await self.shutdown()

        app.router.lifespan_context = lifespan

    async def startup(self) -> None:
        """Start every module, rolling back the ones already started on failure."""
        for name in _SETUP_ORDER:
            component = self._components.get(name)
            if component is None:
                continue
            try:
                await component.startup()
            except Exception:
                await self.shutdown()
                raise
            self._started.append(component)

    async def shutdown(self) -> None:
        """Stop every started module, in reverse order, ignoring nothing."""
        errors: list[Exception] = []
        while self._started:
            component = self._started.pop()
            try:
                await component.shutdown()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("takeless shutdown failed", errors)

    def get_jobs_client(self) -> Any:
        """FastAPI dependency yielding the configured job client."""
        if self.jobs is None:
            raise RuntimeError(
                "takeless: jobs is not configured. Pass jobs={...} to Takeless(...)."
            )
        return self.jobs.client

    async def get_session(self) -> AsyncIterator[Any]:
        """FastAPI dependency yielding a request-scoped database session.

        An async generator rather than a function returning one: FastAPI checks
        `isasyncgenfunction` on what it is given, and a plain function returning
        a generator would be injected as the generator object itself.
        """
        if self.db is None:
            raise RuntimeError(
                "takeless: db is not configured. Pass db={...} to Takeless(...)."
            )
        async for session in self.db.get_session():
            yield session


class _TakelessHandle(Component):
    """Puts the `Takeless` instance on `app.state` under a component name, so
    modules that aggregate over the others (health) can find the whole set."""

    name = "takeless"

    def __init__(self, takeless: Takeless) -> None:
        self.takeless = takeless


def _coerce[ConfigT](
    value: Any, config_cls: type[ConfigT], *, default_on: bool = False
) -> ConfigT | None:
    """Turn `None` / `True` / a dict / a config object into a config or `None`."""
    if value is False:
        return None
    if value is None:
        return config_cls() if default_on else None
    if value is True:
        return config_cls()
    if isinstance(value, config_cls):
        return value
    if isinstance(value, dict):
        return config_cls(**value)
    raise TypeError(
        f"expected a dict, a {config_cls.__name__}, or a bool, got {type(value).__name__}"
    )
