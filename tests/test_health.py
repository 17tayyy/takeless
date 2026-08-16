from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.core.component import Check, Component
from takeless.health import Health, HealthConfig


class Probe(Component):
    name = "probe"

    def __init__(self, healthy: bool = True, detail: str | None = None) -> None:
        self.healthy = healthy
        self.detail = detail

    async def check(self) -> Check:
        return Check(name=self.name, healthy=self.healthy, detail=self.detail)


class Silent(Component):
    """A component with nothing worth probing."""

    name = "silent"


class Exploding(Component):
    name = "exploding"

    async def check(self) -> Check:
        raise ConnectionError("connection refused")


class Hanging(Component):
    name = "hanging"

    async def check(self) -> Check:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")


def build_app(*components: Component, config: HealthConfig | None = None) -> FastAPI:
    app = FastAPI()
    Health(config).setup(app)
    for component in components:
        component.setup(app)
    return app


def test_liveness_never_probes_anything():
    client = TestClient(build_app(Exploding()))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthy_aggregate():
    client = TestClient(build_app(Probe()))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["probe"]["status"] == "ok"
    assert body["checks"]["probe"]["latency_ms"] >= 0


def test_one_unhealthy_component_fails_the_endpoint():
    app = build_app(Probe(healthy=False, detail="replica lag"))
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["probe"]["detail"] == "replica lag"


def test_a_raising_probe_becomes_an_unhealthy_check():
    response = TestClient(build_app(Exploding())).get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["exploding"]["detail"] == (
        "ConnectionError: connection refused"
    )


def test_a_hanging_probe_is_cut_off():
    app = build_app(Hanging(), config=HealthConfig(timeout=0.05))
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert "timed out" in response.json()["checks"]["hanging"]["detail"]


def test_components_with_nothing_to_probe_do_not_appear():
    response = TestClient(build_app(Silent(), Probe())).get("/health")
    assert set(response.json()["checks"]) == {"probe"}


def test_details_can_be_withheld():
    app = build_app(Probe(), config=HealthConfig(include_details=False))
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_health_endpoints_stay_out_of_the_schema():
    paths = TestClient(build_app(Probe())).get("/openapi.json").json()["paths"]
    assert "/health" not in paths


def test_custom_paths():
    config = HealthConfig(path="/_status", liveness_path="/_status/live")
    client = TestClient(build_app(Probe(), config=config))
    assert client.get("/_status").status_code == 200
    assert client.get("/_status/live").status_code == 200
    assert client.get("/health").status_code == 404
