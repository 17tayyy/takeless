from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.docs import Docs, DocsConfig


def build_app(config: DocsConfig | None = None) -> FastAPI:
    app = FastAPI(title="svc")

    @app.get("/items")
    async def items():
        return []

    Docs(config).setup(app)
    return app


def test_scalar_replaces_swagger_in_development():
    client = TestClient(build_app(DocsConfig(environment="development")))
    response = client.get("/docs")
    assert response.status_code == 200
    assert "@scalar/api-reference" in response.text
    assert 'data-url="/openapi.json"' in response.text
    assert "swagger-ui" not in response.text


def test_openapi_is_still_served_when_docs_are_on():
    client = TestClient(build_app(DocsConfig(environment="staging")))
    assert client.get("/openapi.json").status_code == 200


def test_docs_and_schema_both_disappear_in_production():
    client = TestClient(build_app(DocsConfig(environment="production")))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_schema_can_be_kept_while_hiding_the_docs():
    config = DocsConfig(environment="production", hide_openapi_when_disabled=False)
    client = TestClient(build_app(config))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_redoc_is_removed_even_when_docs_are_enabled():
    """FastAPI mounts /redoc by default; only the configured provider survives."""
    client = TestClient(build_app(DocsConfig(environment="development")))
    assert client.get("/redoc").status_code == 404


def test_swagger_provider_brings_the_familiar_page_back():
    config = DocsConfig(environment="development", provider="swagger")
    response = TestClient(build_app(config)).get("/docs")
    assert "swagger-ui" in response.text


def test_custom_path_and_environments():
    config = DocsConfig(
        environment="production", enabled_envs=("production",), path="/reference"
    )
    client = TestClient(build_app(config))
    assert client.get("/reference").status_code == 200
    assert client.get("/docs").status_code == 404


def test_theme_reaches_the_page():
    config = DocsConfig(environment="development", theme="moon")
    assert '"theme":"moon"' in TestClient(build_app(config)).get("/docs").text
