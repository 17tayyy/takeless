"""Producer-side tests.

They never talk to Redis: what takeless owns here is the config translation and
the wrapper's delegation, and both are observable against a stand-in for the
ArdiQ app. A test that needed a broker would be testing ArdiQ, not takeless.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.errors import Errors
from takeless.health import Health
from takeless.jobs import ArdiqClient, Jobs, JobsClient, JobsConfig


class FakeArdiq:
    """The slice of `ardiq.Ardiq` the producer wrapper actually calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, tuple, dict]] = []
        self.refs: list[dict[str, Any]] = []
        self.size = 7

    async def send(self, name: str, *args: Any, **kwargs: Any) -> str:
        self.sent.append((name, args, kwargs))
        return f"job-{len(self.sent)}"

    def ref(self, name: str, *, priority: str | None = None, unique: bool = False):
        self.refs.append({"name": name, "priority": priority, "unique": unique})
        return f"ref:{name}"

    async def queue_size(self) -> int:
        return self.size

    async def status(self, task_id: str) -> str:
        return "queued"

    async def abort(self, task_id: str) -> bool:
        return True


@pytest.fixture
def client() -> ArdiqClient:
    return ArdiqClient(FakeArdiq(), JobsConfig())


async def test_enqueue_passes_the_arguments_through(client: ArdiqClient):
    job = await client.enqueue("generate_report", 1, user_id=42)
    assert job == "job-1"
    assert client.ardiq.sent == [("generate_report", (1,), {"user_id": 42})]


async def test_task_handle_carries_enqueue_options(client: ArdiqClient):
    client.task("send_email", priority="high", unique=True)
    assert client.ardiq.refs == [
        {"name": "send_email", "priority": "high", "unique": True}
    ]


async def test_queue_inspection_delegates(client: ArdiqClient):
    assert await client.queue_size() == 7
    assert await client.status("x") == "queued"
    assert await client.abort("x") is True


def test_redis_url_is_accepted_as_an_alias():
    assert JobsConfig(redis_url="redis://host:1").broker_url == "redis://host:1"
    assert JobsConfig(broker_url="redis://host:2").broker_url == "redis://host:2"


def test_config_defaults():
    config = JobsConfig()
    assert config.queue == "default"
    assert config.priorities is None


async def test_health_reports_the_queue_depth():
    jobs = Jobs.__new__(Jobs)
    jobs.config = JobsConfig(queue="reports")
    jobs.client = ArdiqClient(FakeArdiq(), jobs.config)

    check = await jobs.check()
    assert check is not None
    assert check.healthy
    assert check.meta == {"queue": "reports", "queued": 7}


async def test_health_check_can_be_turned_off():
    jobs = Jobs.__new__(Jobs)
    jobs.config = JobsConfig(health_check=False)
    jobs.client = ArdiqClient(FakeArdiq(), jobs.config)
    assert await jobs.check() is None


def test_the_dependency_yields_the_configured_client():
    app = FastAPI()
    Errors().setup(app)
    Health().setup(app)

    jobs = Jobs.__new__(Jobs)
    jobs.config = JobsConfig()
    jobs.client = ArdiqClient(FakeArdiq(), jobs.config)
    jobs.setup(app)

    @app.post("/reports")
    async def make_report(client: JobsClient):
        job = await client.enqueue("generate_report", user_id=1)
        return {"job": job}

    with TestClient(app) as http:
        assert http.post("/reports").json() == {"job": "job-1"}
        # And configuring jobs is what puts the queue in /health.
        assert http.get("/health").json()["checks"]["jobs"]["queued"] == 7
