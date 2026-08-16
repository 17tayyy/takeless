"""structlog configuration.

Third-party logs (uvicorn, SQLAlchemy) are routed through the same renderer via
`ProcessorFormatter`, so a production log stream is JSON all the way down
instead of JSON with uvicorn's plain text interleaved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

    from takeless.observability.config import ObservabilityConfig

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

#: Marks the handler this module installs, so reconfiguring replaces it instead
#: of stacking a second one.
_HANDLER_NAME = "takeless"


def configure_logging(config: ObservabilityConfig) -> None:
    """Apply `config` process-wide. Idempotent; last call wins."""
    json_logs = True if config.json_logs is None else config.json_logs
    level = logging.getLevelNamesMapping().get(config.level.upper(), logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if config.service:
        shared.append(_static_fields({"service": config.service}))
    if config.static_fields:
        shared.append(_static_fields(config.static_fields))

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any
    if json_logs:
        post: list[Any] = [
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
        ]
        renderer = structlog.processors.JSONRenderer()
    else:
        post = [structlog.processors.UnicodeDecoder()]
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *post,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.set_name(_HANDLER_NAME)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    if config.capture_stdlib_logging:
        for name in _UVICORN_LOGGERS:
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.propagate = True
        logging.getLogger("uvicorn.access").disabled = config.access_log


def _static_fields(fields: dict[str, str]) -> Any:
    """A processor that stamps constant fields onto every event."""

    def processor(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
        for key, value in fields.items():
            event.setdefault(key, value)
        return event

    return processor


def get_logger(name: str | None = None) -> BoundLogger:
    """A structured logger. Carries whatever `bind_context` has bound."""
    return structlog.stdlib.get_logger(name)
