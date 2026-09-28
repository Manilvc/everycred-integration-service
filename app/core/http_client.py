"""Shared outbound HTTP client for calls to external providers.

One :class:`httpx.AsyncClient` is created per process in the application
lifespan and reused for every request, so connection pools and TLS
sessions are shared. Routes and services receive it through the
:data:`HttpClient` dependency.
"""

from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.core.config import Settings
from app.core.context import REQUEST_ID_HEADER, get_request_id


async def _forward_request_id(request: httpx.Request) -> None:
    # Passing our id upstream lets provider-side logs be matched to ours
    # when a partner investigates a failed call.
    request_id = get_request_id()
    if request_id:
        request.headers[REQUEST_ID_HEADER] = request_id


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Build the shared client with timeouts and pool limits applied."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        limits=httpx.Limits(max_connections=settings.http_max_connections),
        headers={
            "User-Agent": (
                f"{settings.service_name}/{settings.service_version}"
            ),
        },
        # Providers should not redirect API calls; following one could
        # send credentials to an unexpected host.
        follow_redirects=False,
        event_hooks={"request": [_forward_request_id]},
    )


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the client created during application startup."""
    return request.state.http_client


HttpClient = Annotated[httpx.AsyncClient, Depends(get_http_client)]
