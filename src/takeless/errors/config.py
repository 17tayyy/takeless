from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ErrorsConfig(BaseModel):
    """Shape and verbosity of error responses."""

    model_config = ConfigDict(extra="forbid")

    #: Top-level key wrapping every error body. `None` puts the fields at the
    #: root instead, for APIs that already have a flat error convention.
    envelope_key: str | None = "error"

    #: Echo the request id into the body, so a user can paste it into a ticket
    #: and it maps straight onto a log line.
    include_request_id: bool = True

    #: Include the `details` payload of `AppError` and validation failures.
    include_details: bool = True

    #: Put the actual exception text of an *unhandled* error in the response.
    #: Off by default: unhandled means unaudited, and those strings leak
    #: internals. Worth turning on in development.
    expose_internal_errors: bool = False

    #: Log every unhandled exception with a traceback, at `error`.
    log_unhandled: bool = True

    #: Log deliberately raised `AppError`s too, at `warning`. Off by default —
    #: a 404 is not an incident.
    log_handled: bool = False

    #: Replace FastAPI's 422 body with the takeless envelope. Turn off if you
    #: have clients parsing FastAPI's native `{"detail": [...]}`.
    handle_validation_errors: bool = True
