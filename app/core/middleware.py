"""ASGI middleware that sets up per-request context.

:class:`RequestContextMiddleware` is written as plain ASGI rather than
with Starlette's ``BaseHTTPMiddleware``, which buffers responses and
breaks context variable propagation in some cases.
"""

import logging
import re
import time
from uuid import uuid4

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import REQUEST_ID_HEADER, request_id_context
from app.core.exceptions import build_error_body

logger = logging.getLogger(__name__)

# Caller-supplied ids are written to logs, so anything outside this
# pattern is replaced rather than trusted (prevents log injection).
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _resolve_request_id(headers: Headers) -> str:
    incoming_id = headers.get(REQUEST_ID_HEADER)
    if incoming_id and _VALID_REQUEST_ID.fullmatch(incoming_id):
        return incoming_id
    return uuid4().hex


class RequestContextMiddleware:
    """Assign a request id, log each request, and catch crashes.

    For every HTTP request this middleware:

    * reuses a valid ``X-Request-ID`` header or generates a new id, and
      echoes it back on the response;
    * logs method, path, status, and duration once the request ends;
    * turns any unhandled exception into a generic 500 response and
      logs the traceback with the request id attached.

    It should be the outermost middleware so that everything else runs
    inside the request context.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(Headers(scope=scope))
        context_token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
                MutableHeaders(scope=message).append(
                    REQUEST_ID_HEADER, request_id
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "Unhandled error on %s %s", scope["method"], scope["path"]
            )
            # Headers are already on the wire, so no error body can be
            # sent. Re-raise and let the server close the connection.
            if response_started:
                raise
            error_response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=build_error_body(
                    "internal_error", "An unexpected error occurred."
                ),
            )
            await error_response(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "%s %s -> %s in %.1f ms",
                scope["method"],
                scope["path"],
                response_status,
                duration_ms,
            )
            request_id_context.reset(context_token)
