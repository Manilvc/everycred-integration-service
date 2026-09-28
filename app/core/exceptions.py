"""Domain exceptions and the handlers that turn them into responses.

Services raise subclasses of :class:`AppError` and never deal with HTTP.
The handlers registered by :func:`register_exception_handlers` convert
those errors, request validation failures, and Starlette HTTP errors
into the shared :class:`~app.shared.schemas.ErrorResponse` envelope.

Unexpected exceptions are handled in
:class:`~app.core.middleware.RequestContextMiddleware` instead, so they
are logged while the request id is still available.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_id
from app.shared.schemas import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that are safe to report to API clients.

    Subclasses set ``status_code`` and ``error_code``. The message is
    returned to the client as-is, so it must not contain secrets,
    personal data, or internal details.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "bad_request"
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(AppError):
    """Raised when the caller's identity cannot be established."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "authentication_failed"
    # RFC 6750 requires 401 responses to name the expected scheme.
    headers = {"WWW-Authenticate": "Bearer"}


class PermissionDeniedError(AppError):
    """Raised when the caller is not allowed to perform the action."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "permission_denied"


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ConflictError(AppError):
    """Raised when the request conflicts with the current state."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ExternalServiceError(AppError):
    """Raised when an upstream provider fails or returns bad data."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "external_service_error"


def build_error_body(
    error_code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready error envelope for the current request."""
    error_response = ErrorResponse(
        error=ErrorDetail(
            code=error_code,
            message=message,
            request_id=get_request_id(),
            details=details,
        )
    )
    return error_response.model_dump(exclude_none=True)


def _summarise_validation_errors(
    exc: RequestValidationError,
) -> list[dict[str, Any]]:
    # Pydantic includes the rejected input in each error. That input can
    # hold personal data or secrets, so only location, message, and type
    # are echoed back.
    return [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Convert an :class:`AppError` into its error response."""
    logger.info("Request failed with %s: %s", exc.error_code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_body(exc.error_code, exc.message),
        headers=exc.headers,
    )


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert a request validation failure into a 422 response."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=build_error_body(
            "validation_error",
            "The request is invalid.",
            details=_summarise_validation_errors(exc),
        ),
    )


async def handle_http_error(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Convert routing and framework HTTP errors, such as 404 or 405."""
    error_code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_body(error_code, str(exc.detail)),
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the service's exception handlers to ``app``."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
