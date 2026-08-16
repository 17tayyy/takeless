from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.observability import (
    Observability,
    ObservabilityConfig,
    bind_context,
    get_context,
    get_logger,
    get_request_id,
)


def build_app(config: ObservabilityConfig | None = None) -> FastAPI:
    app = FastAPI()
    Observability(config or ObservabilityConfig(json_logs=True)).setup(app)

    @app.get("/ping")
    async def ping():
        return {"request_id": get_request_id()}

    @app.get("/with-user")
    async def with_user():
        bind_context(user_id="u-1")
        get_logger("test").info("did_a_thing")
        return {"context": get_context()}

    return app


def test_request_id_is_generated_and_echoed():
    with TestClient(build_app()) as client:
        response = client.get("/ping")
    header = response.headers["x-request-id"]
    assert header
    assert response.json()["request_id"] == header


def test_incoming_request_id_is_reused():
    with TestClient(build_app()) as client:
        response = client.get("/ping", headers={"X-Request-ID": "from-gateway"})
    assert response.headers["x-request-id"] == "from-gateway"
    assert response.json()["request_id"] == "from-gateway"


def test_incoming_request_id_can_be_refused():
    config = ObservabilityConfig(json_logs=True, trust_incoming_request_id=False)
    with TestClient(build_app(config)) as client:
        response = client.get("/ping", headers={"X-Request-ID": "forged"})
    assert response.headers["x-request-id"] != "forged"


def test_context_bound_in_a_handler_is_visible_to_the_logger():
    with TestClient(build_app()) as client:
        context = client.get("/with-user").json()["context"]
    assert context["user_id"] == "u-1"
    assert context["request_id"]


def test_context_does_not_leak_between_requests():
    with TestClient(build_app()) as client:
        first = client.get("/with-user").json()["context"]
        second = client.get("/ping").json()["request_id"]
    assert first["request_id"] != second


def test_access_log_line_carries_the_bound_user(caplog):
    """The line is emitted by the middleware after the handler has run, which is
    only possible because the middleware shares the handler's context."""
    with (
        caplog.at_level(logging.INFO, logger="takeless.access"),
        TestClient(build_app()) as client,
    ):
        client.get("/with-user")

    records = [r for r in caplog.records if r.name == "takeless.access"]
    assert records, "no access log line was emitted"
    # structlog hands the stdlib logger the event dict itself; rendering happens
    # in the formatter, which caplog does not run.
    event = records[-1].msg
    assert event["event"] == "request"
    assert event["path"] == "/with-user"
    assert event["status"] == 200
    assert event["user_id"] == "u-1"
    assert event["request_id"]
    assert isinstance(event["duration_ms"], float)


def test_excluded_paths_are_not_logged(caplog):
    config = ObservabilityConfig(json_logs=True, exclude_paths=("/ping",))
    with (
        caplog.at_level(logging.INFO, logger="takeless.access"),
        TestClient(build_app(config)) as client,
    ):
        client.get("/ping")
    assert not [r for r in caplog.records if r.name == "takeless.access"]


def test_access_log_can_be_turned_off(caplog):
    config = ObservabilityConfig(json_logs=True, access_log=False)
    with (
        caplog.at_level(logging.INFO, logger="takeless.access"),
        TestClient(build_app(config)) as client,
    ):
        client.get("/ping")
    assert not [r for r in caplog.records if r.name == "takeless.access"]


def test_static_fields_are_stamped_on_every_line(caplog):
    config = ObservabilityConfig(
        json_logs=True, service="billing", static_fields={"region": "eu-west-1"}
    )
    with (
        caplog.at_level(logging.INFO, logger="takeless.access"),
        TestClient(build_app(config)) as client,
    ):
        client.get("/ping")
    event = [r for r in caplog.records if r.name == "takeless.access"][-1].msg
    assert event["service"] == "billing"
    assert event["region"] == "eu-west-1"
