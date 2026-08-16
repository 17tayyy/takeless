"""Structured logging with request context injected automatically.

Standalone:

    from takeless.observability import Observability, ObservabilityConfig

    obs = Observability(ObservabilityConfig(level="INFO", json_logs=True))
    obs.setup(app)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from takeless.core.component import Component
from takeless.observability.config import ObservabilityConfig
from takeless.observability.context import (
    bind_context,
    clear_context,
    get_context,
    get_request_id,
    set_request_id,
    unbind_context,
)
from takeless.observability.logging import configure_logging, get_logger
from takeless.observability.middleware import RequestContextMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "Observability",
    "ObservabilityConfig",
    "RequestContextMiddleware",
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_context",
    "get_logger",
    "get_request_id",
    "set_request_id",
    "unbind_context",
]


class Observability(Component):
    """Configures structlog on construction, and binds request context on setup.

    Logging is applied in `__init__` rather than in `setup()` on purpose: every
    other module logs while it is being built, and those lines should already be
    in the right format.
    """

    name = "observability"

    def __init__(self, config: ObservabilityConfig | None = None) -> None:
        self.config = config or ObservabilityConfig()
        configure_logging(self.config)
        self.logger = get_logger(self.config.service or "takeless")

    def setup(self, app: FastAPI) -> None:
        super().setup(app)
        app.add_middleware(RequestContextMiddleware, config=self.config)
