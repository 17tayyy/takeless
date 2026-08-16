"""Exception handlers producing one response shape for every failure mode.

Four sources of errors are folded into the same envelope: `AppError` (raised by
you), `HTTPException` (raised by FastAPI and by third-party dependencies),
request validation failures, and anything unhandled.
"""

from __future__ import annotations

import http
import re
from typing import TYPE_CHECKING, Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from takeless.errors.exceptions import AppError, ValidationError
from takeless.observability.context import get_request_id
from takeless.observability.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request

    from takeless.errors.config import ErrorsConfig

_logger = get_logger("takeless.errors")


def build_response(
    config: ErrorsConfig,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render one error into the configured envelope."""
    body: dict[str, Any] = {"code": code, "message": message}
    if config.include_details and details is not None:
        body["details"] = jsonable_encoder(details)
    if config.include_request_id:
        request_id = get_request_id()
        if request_id is not None:
            body["request_id"] = request_id

    payload = {config.envelope_key: body} if config.envelope_key else body
    return JSONResponse(payload, status_code=status_code, headers=headers or None)


def install_handlers(app: FastAPI, config: ErrorsConfig) -> None:
    """Register every handler on `app`."""

    async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, AppError)
        if config.log_handled:
            _logger.warning(
                "request_failed",
                error_code=exc.code,
                status=exc.status_code,
                error_message=exc.message,
            )
        return build_response(
            config,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            headers=exc.headers,
        )

    async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        detail = exc.detail

        message = detail if isinstance(detail, str) else _phrase(exc.status_code)
        details = None if isinstance(detail, str) else detail
        return build_response(
            config,
            status_code=exc.status_code,
            code=_code_for(exc.status_code),
            message=message,
            details=details,
            headers=getattr(exc, "headers", None),
        )

    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        return build_response(
            config,
            status_code=ValidationError.status_code,
            code=ValidationError.code,
            message=ValidationError.message,
            details=exc.errors(),
        )

    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        if config.log_unhandled:
            _logger.exception(
                "unhandled_exception",
                exc_type=type(exc).__name__,
                path=request.url.path,
            )
        message = str(exc) if config.expose_internal_errors else AppError.message
        return build_response(
            config,
            status_code=500,
            code="internal_error",
            message=message,
        )

    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    if config.handle_validation_errors:
        app.add_exception_handler(RequestValidationError, handle_validation_error)

    app.add_exception_handler(Exception, handle_unexpected)


def _phrase(status_code: int) -> str:
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _code_for(status_code: int) -> str:
    """A stable machine code for a status raised outside our hierarchy.

    Everything that is not a letter or digit collapses to an underscore, so the
    codes stay safe to switch on ("I'm a Teapot" -> "i_m_a_teapot").
    """
    return re.sub(r"[^a-z0-9]+", "_", _phrase(status_code).lower()).strip("_")
