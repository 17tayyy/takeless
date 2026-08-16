"""CORS with defaults that are safe rather than permissive.

Standalone:

    from takeless.cors import Cors, CorsConfig

    Cors(CorsConfig(allow_origins=["https://myapp.com"])).setup(app)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from takeless.core.component import Component

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["Cors", "CorsConfig"]


class CorsConfig(BaseModel):
    """Which cross-origin callers are allowed, and what they may send."""

    model_config = ConfigDict(extra="forbid")

    #: No origin is allowed until you name one. An empty list means the
    #: middleware is installed but rejects everything, which is the correct
    #: default for a service nobody has told you is browser-facing.
    allow_origins: list[str] = Field(default_factory=list)
    allow_origin_regex: str | None = None

    allow_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    allow_headers: list[str] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "X-Request-ID"]
    )
    allow_credentials: bool = False

    #: Surfaced to browser JS. The request id is here so a frontend can attach
    #: it to a bug report.
    expose_headers: list[str] = Field(default_factory=lambda: ["X-Request-ID"])

    max_age: int = 600

    @model_validator(mode="after")
    def _reject_credentialed_wildcard(self) -> CorsConfig:
        if self.allow_credentials and "*" in self.allow_origins:
            raise ValueError(
                "cors: allow_origins=['*'] with allow_credentials=True lets any "
                "site on the internet make authenticated requests as your users. "
                "List the origins explicitly, or use allow_origin_regex."
            )
        return self


class Cors(Component):
    """Installs Starlette's CORS middleware from `CorsConfig`."""

    name = "cors"

    def __init__(self, config: CorsConfig | None = None) -> None:
        self.config = config or CorsConfig()

    def setup(self, app: FastAPI) -> None:
        from starlette.middleware.cors import CORSMiddleware

        super().setup(app)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self.config.allow_origins,
            allow_origin_regex=self.config.allow_origin_regex,
            allow_methods=self.config.allow_methods,
            allow_headers=self.config.allow_headers,
            allow_credentials=self.config.allow_credentials,
            expose_headers=self.config.expose_headers,
            max_age=self.config.max_age,
        )
