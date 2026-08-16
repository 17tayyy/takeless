from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ObservabilityConfig(BaseModel):
    """How logs are rendered and what request context they carry."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"

    #: `None` means "decide from the environment": JSON everywhere but
    #: development, where the console renderer is easier to read.
    json_logs: bool | None = None

    #: Stamped on every line. Defaults to the app name from settings.
    service: str | None = None

    request_id_header: str = "X-Request-ID"

    #: Reuse a request id supplied by the caller (a gateway or upstream
    #: service), so one id spans the whole hop chain. Turn off at the edge of
    #: an untrusted network, where a client could forge or collide ids.
    trust_incoming_request_id: bool = True

    #: Emit one line per finished request, with method, path, status, duration.
    access_log: bool = True

    #: Paths that never produce an access log line. Probes hit these every few
    #: seconds and drown everything else.
    exclude_paths: tuple[str, ...] = ("/health", "/health/live", "/health/ready")

    #: Route uvicorn's loggers through this configuration and silence its own
    #: access log, which would otherwise duplicate ours in a different format.
    capture_stdlib_logging: bool = True

    #: Bound onto every line, for fields that never change (version, region).
    static_fields: dict[str, str] = Field(default_factory=dict)
