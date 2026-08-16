from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.cors import Cors, CorsConfig
from takeless.errors import Errors, NotFound


def build_app(config: CorsConfig) -> FastAPI:
    app = FastAPI()
    Errors().setup(app)
    Cors(config).setup(app)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/missing")
    async def missing():
        raise NotFound()

    return app


def test_allowed_origin_gets_the_header():
    client = TestClient(build_app(CorsConfig(allow_origins=["https://myapp.com"])))
    response = client.get("/ping", headers={"Origin": "https://myapp.com"})
    assert response.headers["access-control-allow-origin"] == "https://myapp.com"


def test_unlisted_origin_gets_nothing():
    client = TestClient(build_app(CorsConfig(allow_origins=["https://myapp.com"])))
    response = client.get("/ping", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_empty_allow_list_refuses_every_origin():
    client = TestClient(build_app(CorsConfig()))
    response = client.get("/ping", headers={"Origin": "https://myapp.com"})
    assert "access-control-allow-origin" not in response.headers


def test_error_responses_still_carry_cors_headers():
    """CORS sits outside the exception handlers, so a browser can read the 404."""
    client = TestClient(build_app(CorsConfig(allow_origins=["https://myapp.com"])))
    response = client.get("/missing", headers={"Origin": "https://myapp.com"})
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "https://myapp.com"


def test_preflight():
    config = CorsConfig(allow_origins=["https://myapp.com"])
    response = TestClient(build_app(config)).options(
        "/ping",
        headers={
            "Origin": "https://myapp.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_request_id_is_exposed_to_the_browser():
    config = CorsConfig(allow_origins=["https://myapp.com"])
    response = TestClient(build_app(config)).get(
        "/ping", headers={"Origin": "https://myapp.com"}
    )
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]


def test_credentialed_wildcard_is_refused_at_config_time():
    with pytest.raises(ValueError, match="any site on the internet"):
        CorsConfig(allow_origins=["*"], allow_credentials=True)
