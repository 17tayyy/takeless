"""Standardised error handling: one JSON shape for every failure.

    {"error": {"code": "not_found", "message": "...", "request_id": "..."}}

Standalone:

    from takeless.errors import Errors, NotFound

    Errors().setup(app)

    @app.get("/items/{id}")
    async def get_item(id: str):
        raise NotFound("No item with that id.", details={"id": id})
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from takeless.core.component import Component
from takeless.errors.config import ErrorsConfig
from takeless.errors.exceptions import (
    AppError,
    BadRequest,
    Conflict,
    Forbidden,
    InternalError,
    NotFound,
    ServiceUnavailable,
    TakelessError,
    TooManyRequests,
    Unauthorized,
    ValidationError,
)
from takeless.errors.handlers import build_response, install_handlers

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "AppError",
    "BadRequest",
    "Conflict",
    "Errors",
    "ErrorsConfig",
    "Forbidden",
    "InternalError",
    "NotFound",
    "ServiceUnavailable",
    "TakelessError",
    "TooManyRequests",
    "Unauthorized",
    "ValidationError",
    "build_response",
    "install_handlers",
]


class Errors(Component):
    """Installs the exception handlers."""

    name = "errors"

    def __init__(self, config: ErrorsConfig | None = None) -> None:
        self.config = config or ErrorsConfig()

    def setup(self, app: FastAPI) -> None:
        super().setup(app)
        install_handlers(app, self.config)
