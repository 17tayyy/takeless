"""The contract every takeless module implements.

A component is a self-contained module — it configures itself from its own
config object, hooks itself into a FastAPI app, and owns its own lifecycle and
health probe. `Takeless` is only a constructor that builds a set of components
and calls these four methods on each; it holds no logic of its own. That is
what keeps the two usage paths from diverging: the modular path calls
`Auth(...).setup(app)` directly, and the `Takeless` path calls the very same
method.

Components register themselves on `app.state` so that module-level dependencies
(`require_auth`, `get_session`, ...) can find them from a request without a
process-wide global — two apps in one process stay independent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

_STATE_KEY = "takeless_components"


@dataclass(slots=True, frozen=True)
class Check:
    """One component's answer to "are you working?"."""

    name: str
    healthy: bool
    detail: str | None = None
    latency_ms: float | None = None
    meta: dict[str, object] = field(default_factory=dict)


class Component:
    """Base class for every takeless module.

    Subclasses override only what they need; the defaults are no-ops, so a
    purely synchronous module (CORS, errors) implements `setup` alone.
    """

    #: Stable key this component is registered under on `app.state`.
    name: ClassVar[str] = "component"

    def setup(self, app: FastAPI) -> None:
        """Hook into `app`: middleware, routes, exception handlers, state.

        Subclasses must call `super().setup(app)` so the component stays
        reachable from request-scoped dependencies and its lifecycle runs.
        """
        register(app, self)
        if getattr(app.state, "takeless", None) is None:
            bind_lifecycle(app, self)

    async def startup(self) -> None:
        """Open connections. Runs inside the app's lifespan, before serving."""

    async def shutdown(self) -> None:
        """Close connections. Runs on lifespan exit, even if startup failed."""

    async def check(self) -> Check | None:
        """Probe the backing service. `None` means "nothing to probe here"."""
        return None


def bind_lifecycle(app: FastAPI, component: Component) -> None:
    """Run `component`'s startup and shutdown with the app's lifespan.

    Wraps the existing lifespan rather than replacing it, so several components
    can each bind their own and a user-supplied `lifespan=` still runs — theirs
    innermost, with connections already open.
    """
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
        await component.startup()
        try:
            async with previous(app) as state:
                yield state
        finally:
            await component.shutdown()

    app.router.lifespan_context = lifespan


def register(app: FastAPI, component: Component) -> None:
    """Make `component` reachable from `app`."""
    components: dict[str, Component] = getattr(app.state, _STATE_KEY, None) or {}
    components[component.name] = component
    setattr(app.state, _STATE_KEY, components)


def components_of(app: FastAPI) -> dict[str, Component]:
    """Every component registered on `app`, keyed by `Component.name`."""
    return dict(getattr(app.state, _STATE_KEY, None) or {})


def get_component[C: Component](app: FastAPI, kind: type[C]) -> C:
    """The registered component of type `kind`, or a message saying how to add it.

    Dependencies call this, so the error lands on the request that needed the
    module rather than at import time — which is what lets `require_auth` be a
    plain importable dependency instead of something you build per app.
    """
    found = getattr(app.state, _STATE_KEY, None) or {}
    component = found.get(kind.name)
    if isinstance(component, kind):
        return component
    raise RuntimeError(
        f"takeless: the {kind.name!r} module is not set up on this app. "
        f"Either pass {kind.name}=... to Takeless(...) and call takeless.setup(app), "
        f"or set it up on its own with {kind.__name__}(...).setup(app)."
    )
