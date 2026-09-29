"""Signing, destination checks, and sending for webhooks.

Every request carries::

    X-EveryCRED-Event: session.completed
    X-EveryCRED-Delivery: <delivery id>
    X-EveryCRED-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>

where the HMAC covers ``"<t>.<raw body>"`` with the client's webhook
secret. Receivers recompute it, compare in constant time, and reject
timestamps older than a few minutes to stop replays (see
:func:`verify_signature`).
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

EVENT_HEADER = "X-EveryCRED-Event"
DELIVERY_HEADER = "X-EveryCRED-Delivery"
SIGNATURE_HEADER = "X-EveryCRED-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


class UnsafeDestinationError(Exception):
    """Raised when a webhook URL points somewhere it must not."""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of one delivery attempt."""

    delivered: bool
    status_code: int | None
    error: str | None
    retryable: bool = True


def encode_body(payload: dict[str, Any]) -> bytes:
    """Serialise a payload exactly as it is signed and sent."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """Return the signature header value for ``body``."""
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(
    secret: str,
    header: str,
    body: bytes,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Check a received signature; what a receiver should implement."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, received)


def check_url_shape(url: str, allow_private_networks: bool) -> None:
    """Validate a webhook URL before it is saved.

    Raises:
        UnsafeDestinationError: Not HTTPS (plain HTTP is allowed only
            when private networks are allowed, i.e. local setups), has
            credentials in it, or has no host.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" and not (
        parts.scheme == "http" and allow_private_networks
    ):
        raise UnsafeDestinationError("the webhook URL must use https")
    if not parts.hostname:
        raise UnsafeDestinationError("the webhook URL must have a host")
    if parts.username or parts.password:
        raise UnsafeDestinationError(
            "the webhook URL must not contain credentials"
        )


async def check_destination(url: str, allow_private_networks: bool) -> None:
    """Refuse destinations inside private networks, unless allowed.

    The host is resolved and every address checked, so a public name
    that resolves to an internal address is refused too.

    Raises:
        UnsafeDestinationError: The host resolves to a private,
            loopback, link-local, or otherwise non-public address, or
            cannot be resolved.
    """
    if allow_private_networks:
        return
    host = urlsplit(url).hostname or ""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as exc:
        raise UnsafeDestinationError(f"cannot resolve {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise UnsafeDestinationError(
                f"{host} resolves to a non-public address"
            )


async def send(
    http_client: httpx.AsyncClient,
    *,
    url: str,
    secret: str,
    event: str,
    delivery_id: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    allow_private_networks: bool,
) -> DeliveryResult:
    """Sign and POST one event. Never raises; the result says what happened."""
    try:
        check_url_shape(url, allow_private_networks)
        await check_destination(url, allow_private_networks)
    except UnsafeDestinationError as exc:
        return DeliveryResult(False, None, str(exc), retryable=False)

    body = encode_body(payload)
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id,
        SIGNATURE_HEADER: sign(secret, int(time.time()), body),
    }
    try:
        response = await http_client.post(
            url,
            content=body,
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        return DeliveryResult(False, None, "timed out")
    except httpx.HTTPError as exc:
        return DeliveryResult(False, None, type(exc).__name__)

    if response.is_success:
        return DeliveryResult(True, response.status_code, None)
    return DeliveryResult(
        False,
        response.status_code,
        f"endpoint answered HTTP {response.status_code}",
        # A 4xx other than 408/429 will not fix itself by retrying.
        retryable=response.status_code >= 500
        or response.status_code in (408, 429),
    )
