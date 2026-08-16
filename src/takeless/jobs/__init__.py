"""Background jobs, backed by ArdiQ.

Requires `pip install 'takeless[jobs]'`.

This is the producer half only. Your API enqueues by *name*, so it never
imports the task module and never drags the worker's dependencies into the web
process — the worker lives in its own file, run with `ardiq run worker:app`.

Standalone:

    from takeless.jobs import Jobs, JobsConfig

    jobs = Jobs(JobsConfig(broker_url="redis://localhost:6379", queue="default"))
    jobs.setup(app)

    @app.post("/reports")
    async def make_report(client: JobsClient):
        job = await client.enqueue("generate_report", user_id=1)
        return {"job_id": job.task_id}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from takeless.core.component import Check, Component, get_component
from takeless.core.deps import require_dependency

if TYPE_CHECKING:
    from ardiq import Ardiq, Job, PreparedTask, Task, TaskInfo, TaskResult


ardiq = require_dependency("ardiq")

__all__ = [
    "ArdiqClient",
    "Jobs",
    "JobsClient",
    "JobsConfig",
    "get_jobs_client",
]


class JobsConfig(BaseModel):
    """Connection and queue settings for the ArdiQ producer."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Redis URL the queue lives on. `redis_url` is accepted as an alias, since
    #: that is what ArdiQ itself calls it.
    broker_url: str = Field(default="redis://localhost:6379", alias="redis_url")

    queue: str = "default"

    #: Lane names, highest priority first. `None` keeps ArdiQ's defaults.
    priorities: list[str] | None = None

    #: Lane used when a job does not name one. `None` means ArdiQ's middle lane.
    default_priority: str | None = None

    #: Count queued jobs in `/health`. Cheap, but it is a Redis round trip on
    #: every probe; turn it off if your probes are aggressive.
    health_check: bool = True


class ArdiqClient:
    """The enqueue-side of ArdiQ, already connected.

    Wraps `Ardiq` rather than exposing it so that the API surface here is only
    the producer half — nothing on this object can accidentally start a worker
    loop inside a web process.
    """

    def __init__(self, app: Ardiq, config: JobsConfig) -> None:
        self._app = app
        self.config = config

    @property
    def ardiq(self) -> Ardiq:
        """The underlying ArdiQ app, for anything this wrapper does not cover."""
        return self._app

    async def enqueue(self, name: str, *args: Any, **kwargs: Any) -> Job:
        """Queue task `name` with these arguments.

        Nothing validates `name` here — an unknown task fails on the worker,
        because the whole point is that this process does not import it.
        """
        return await self._app.send(name, *args, **kwargs)

    def task(
        self, name: str, *, priority: str | None = None, unique: bool = False
    ) -> Task[..., Any]:
        """A handle to a worker-side task, for enqueue options.

        await client.task("send_email").options(delay_ms=60_000).enqueue(to=...)
        """
        return self._app.ref(name, priority=priority, unique=unique)

    async def enqueue_many(self, prepared: list[PreparedTask]) -> list[Job]:
        """Queue a batch in one round trip. Build the items with `.prepare(...)`."""
        return await self._app.enqueue_many(prepared)

    async def result(
        self, task_id: str, timeout: float | None = None
    ) -> TaskResult | None:
        return await self._app.result(task_id, timeout)

    async def status(self, task_id: str) -> str:
        return await self._app.status(task_id)

    async def info(self, task_id: str) -> TaskInfo | None:
        return await self._app.info(task_id)

    async def abort(self, task_id: str) -> bool:
        return await self._app.abort(task_id)

    async def queue_size(self) -> int:
        return await self._app.queue_size()


class Jobs(Component):
    """Owns the ArdiQ producer and reports the queue in `/health`."""

    name = "jobs"

    def __init__(self, config: JobsConfig) -> None:
        self.config = config
        options: dict[str, Any] = {
            "redis_url": config.broker_url,
            "queue_name": config.queue,
        }
        if config.priorities is not None:
            options["priorities"] = config.priorities
        if config.default_priority is not None:
            options["default_priority"] = config.default_priority
        self.client = ArdiqClient(ardiq.Ardiq(**options), config)

    async def check(self) -> Check | None:
        if not self.config.health_check:
            return None
        queued = await self.client.queue_size()
        return Check(
            name=self.name,
            healthy=True,
            meta={"queue": self.config.queue, "queued": queued},
        )


def get_jobs_client(request: Request) -> ArdiqClient:
    """FastAPI dependency yielding the configured client."""
    return get_component(request.app, Jobs).client


#: `async def handler(jobs: JobsClient): ...`
JobsClient = Annotated[ArdiqClient, Depends(get_jobs_client)]
