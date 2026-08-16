"""API documentation: Scalar instead of Swagger UI, gated by environment.

Two things happen here. FastAPI's built-in `/docs` and `/redoc` are replaced by
a Scalar reference, and in an environment that is not on the allow-list the
documentation *and* the OpenAPI schema are removed from the app entirely — not
hidden behind a 403, removed, so there is no schema left to leak.

Standalone:

    from takeless.docs import Docs, DocsConfig

    Docs(DocsConfig(environment="production")).setup(app)   # -> 404 on /docs
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict
from starlette.responses import HTMLResponse

from takeless.core.component import Component

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["Docs", "DocsConfig", "render_scalar"]


class DocsConfig(BaseModel):
    """Where the docs live and who is allowed to see them."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["scalar", "swagger", "redoc"] = "scalar"

    path: str = "/docs"

    #: Filled in from settings when used through `Takeless`.
    environment: str | None = None

    #: Production is absent on purpose. A public schema is a map of your API
    #: for anyone enumerating it; opt in per environment if you want it.
    enabled_envs: tuple[str, ...] = ("development", "staging", "test")

    #: Remove the OpenAPI route too when the docs are disabled. Turn off if a
    #: client generator or gateway fetches the schema from production.
    hide_openapi_when_disabled: bool = True

    #: Scalar's bundle. Pin a version here if you would rather not track latest.
    scalar_js_url: str = "https://cdn.jsdelivr.net/npm/@scalar/api-reference"

    #: Scalar's built-in themes: "default", "moon", "purple", "solarized"...
    theme: str = "default"

    title: str | None = None


class Docs(Component):
    """Swaps FastAPI's docs for the configured provider, or removes them."""

    name = "docs"

    def __init__(self, config: DocsConfig | None = None) -> None:
        self.config = config or DocsConfig()

    @property
    def enabled(self) -> bool:
        environment = self.config.environment or "development"
        return environment in self.config.enabled_envs

    def setup(self, app: FastAPI) -> None:
        super().setup(app)

        _drop_routes(app, {app.docs_url, app.redoc_url, "/docs/oauth2-redirect"})

        if not self.enabled:
            if self.config.hide_openapi_when_disabled:
                _drop_routes(app, {app.openapi_url})
            return

        openapi_url = app.openapi_url or "/openapi.json"
        title = self.config.title or f"{app.title} — API reference"
        provider = self.config.provider
        config = self.config

        async def documentation() -> HTMLResponse:
            if provider == "scalar":
                html = render_scalar(
                    openapi_url=openapi_url,
                    title=title,
                    js_url=config.scalar_js_url,
                    theme=config.theme,
                )
            else:
                html = _render_fastapi_native(provider, openapi_url, title)
            return HTMLResponse(html)

        app.get(config.path, include_in_schema=False)(documentation)


def render_scalar(
    *, openapi_url: str, title: str, js_url: str, theme: str = "default"
) -> str:
    """The Scalar API reference page for `openapi_url`."""
    return f"""\
<!doctype html>
<html>
  <head>
    <title>{title}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow" />
  </head>
  <body>
    <script
      id="api-reference"
      data-url="{openapi_url}"
      data-configuration='{{"theme":"{theme}"}}'></script>
    <script src="{js_url}"></script>
  </body>
</html>
"""


def _render_fastapi_native(provider: str, openapi_url: str, title: str) -> str:
    """Swagger UI or ReDoc, for teams that want the familiar page back."""
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html

    if provider == "redoc":
        page = get_redoc_html(openapi_url=openapi_url, title=title)
    else:
        page = get_swagger_ui_html(openapi_url=openapi_url, title=title)
    return bytes(page.body).decode()


def _drop_routes(app: FastAPI, paths: set[str | None]) -> None:
    """Remove routes by path, ignoring the `None`s that mean "never added"."""
    wanted = {path for path in paths if path}
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in wanted
    ]
