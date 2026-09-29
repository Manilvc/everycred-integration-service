"""OAuth 2.0 client credentials tokens for configured connectors.

Tokens are cached in memory per client and tool until shortly before
they expire, so a burst of operations costs one token request. The
cache key includes a hash of the client secret, so rotating the secret
takes effect on the next call. Each worker process has its own cache.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.base import ConnectorError
from app.connectors.http.config import OAuth2ClientCredentials
from app.connectors.http.templates import render, url_encode_values

logger = logging.getLogger(__name__)

# Refresh this long before expiry so a token never lapses mid-request.
EXPIRY_MARGIN_SECONDS = 60
DEFAULT_EXPIRES_IN_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class _CachedToken:
    access_token: str
    expires_at: float


class TokenCache:
    """In-memory token cache with one in-flight request per key."""

    def __init__(self) -> None:
        self._tokens: dict[str, _CachedToken] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(
        self,
        key: str,
        fetch: Callable[[], Awaitable[tuple[str, int]]],
    ) -> str:
        """Return a valid token for ``key``, fetching one if needed."""
        cached = self._tokens.get(key)
        if cached and cached.expires_at > time.monotonic():
            return cached.access_token
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another request may have refreshed it while we waited.
            cached = self._tokens.get(key)
            if cached and cached.expires_at > time.monotonic():
                return cached.access_token
            access_token, expires_in = await fetch()
            lifetime = max(expires_in - EXPIRY_MARGIN_SECONDS, 0)
            self._tokens[key] = _CachedToken(
                access_token, time.monotonic() + lifetime
            )
            return access_token

    def invalidate(self, key: str) -> None:
        """Forget the token for ``key``, e.g. after the provider says 401."""
        self._tokens.pop(key, None)


token_cache = TokenCache()


def cache_key(
    tool_code: str,
    client_id: object,
    auth: OAuth2ClientCredentials,
    values: dict[str, Any],
) -> str:
    """Return the cache key for one client's token at one tool."""
    rendered = render(
        [auth.token_url, auth.client_id, auth.client_secret, auth.scope],
        values,
    )
    fingerprint = hashlib.sha256(repr(rendered).encode()).hexdigest()
    return f"{tool_code}:{client_id}:{fingerprint}"


async def request_token(
    http_client: httpx.AsyncClient,
    auth: OAuth2ClientCredentials,
    values: dict[str, Any],
    timeout_seconds: float,
) -> tuple[str, int]:
    """Request a token and return it with its lifetime in seconds.

    Raises:
        MissingParametersError: A credential or setting is missing.
        ConnectorError: The token endpoint failed or refused. The
            message never contains the endpoint's response body.
    """
    token_url = render(auth.token_url, url_encode_values(values))
    client_id = str(render(auth.client_id, values))
    client_secret = str(render(auth.client_secret, values))
    form: dict[str, str] = {"grant_type": "client_credentials"}
    if auth.scope:
        form["scope"] = auth.scope
    if auth.audience:
        form["audience"] = auth.audience
    basic_auth: tuple[str, str] | None = None
    if auth.client_auth == "basic":
        basic_auth = (client_id, client_secret)
    else:
        form["client_id"] = client_id
        form["client_secret"] = client_secret

    try:
        response = await http_client.post(
            token_url, data=form, auth=basic_auth, timeout=timeout_seconds
        )
    except httpx.TimeoutException as exc:
        raise ConnectorError(
            "The token endpoint did not respond in time."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Token request failed: %s", type(exc).__name__)
        raise ConnectorError(
            "The token endpoint could not be reached."
        ) from exc

    if response.status_code in (400, 401, 403):
        raise ConnectorError("The provider rejected the client credentials.")
    if not response.is_success:
        raise ConnectorError(
            f"The token endpoint returned HTTP {response.status_code}."
        )
    try:
        payload = response.json()
        access_token = payload["access_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ConnectorError(
            "The token endpoint returned an unexpected response."
        ) from exc
    expires_in = payload.get("expires_in", DEFAULT_EXPIRES_IN_SECONDS)
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = DEFAULT_EXPIRES_IN_SECONDS
    return str(access_token), expires_in
