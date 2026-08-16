from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from takeless.errors import Errors, ErrorsConfig, NotFound
from takeless.observability import Observability, ObservabilityConfig


class Payload(BaseModel):
    count: int


def build_app(config: ErrorsConfig | None = None) -> FastAPI:
    app = FastAPI()
    Observability(ObservabilityConfig(json_logs=True, access_log=False)).setup(app)
    Errors(config).setup(app)

    @app.get("/missing")
    async def missing():
        raise NotFound("No project with that id.", details={"id": "p1"})

    @app.get("/http-exception")
    async def http_exception():
        raise HTTPException(status_code=418, detail="I am a teapot")

    @app.post("/validated")
    async def validated(payload: Payload):
        return payload

    @app.get("/boom")
    async def boom():
        raise ValueError("internal detail that should not leak")

    return app


def test_app_error_envelope():
    with TestClient(build_app()) as client:
        response = client.get("/missing")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    assert body["message"] == "No project with that id."
    assert body["details"] == {"id": "p1"}
    assert body["request_id"]


def test_http_exception_uses_the_same_envelope():
    with TestClient(build_app()) as client:
        response = client.get("/http-exception")
    assert response.status_code == 418
    assert response.json()["error"] == {
        "code": "i_m_a_teapot",
        "message": "I am a teapot",
        "request_id": response.headers["x-request-id"],
    }


def test_validation_errors_are_converted():
    with TestClient(build_app()) as client:
        response = client.post("/validated", json={"count": "not a number"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["body", "count"]


def test_unhandled_exception_does_not_leak_its_message():
    with TestClient(build_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "internal_error"
    assert "internal detail" not in body["message"]


def test_unhandled_exception_can_be_exposed_in_development():
    app = build_app(ErrorsConfig(expose_internal_errors=True))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert "internal detail" in response.json()["error"]["message"]


def test_envelope_can_be_flattened():
    app = build_app(ErrorsConfig(envelope_key=None, include_request_id=False))
    with TestClient(app) as client:
        response = client.get("/missing")
    assert response.json() == {
        "code": "not_found",
        "message": "No project with that id.",
        "details": {"id": "p1"},
    }


def test_details_can_be_withheld():
    app = build_app(ErrorsConfig(include_details=False))
    with TestClient(app) as client:
        response = client.get("/missing")
    assert "details" not in response.json()["error"]


@pytest.mark.parametrize(
    ("exception", "status", "code"),
    [
        ("BadRequest", 400, "bad_request"),
        ("Unauthorized", 401, "unauthorized"),
        ("Forbidden", 403, "forbidden"),
        ("Conflict", 409, "conflict"),
        ("TooManyRequests", 429, "too_many_requests"),
        ("ServiceUnavailable", 503, "service_unavailable"),
    ],
)
def test_hierarchy_status_codes(exception, status, code):
    import takeless.errors as errors_module

    error = getattr(errors_module, exception)()
    assert error.status_code == status
    assert error.code == code
