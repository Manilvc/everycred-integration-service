import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.models import utc_now
from app.core.secret_store import build_secret_store
from app.features.webhooks import delivery as webhook_delivery
from app.features.webhooks.models import DeliveryStatus, WebhookDelivery
from app.features.webhooks.repository import (
    ClientWebhookRepository,
    WebhookDeliveryRepository,
)
from app.features.webhooks.service import WebhookService, retry_delay
from tests.features.clients.conftest import create_client, issue_api_key

WEBHOOK_URL = "/v1/client/webhook"
PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Resolve hosts without the network; map names to addresses."""
    addresses = {"hooks.example.com": PUBLIC_ADDRESS}

    async def fake_getaddrinfo(self: Any, host: str, *_: Any, **__: Any):
        if host not in addresses:
            raise OSError("unknown host")
        return [(2, 1, 6, "", (addresses[host], 0))]

    monkeypatch.setattr(
        "asyncio.base_events.BaseEventLoop.getaddrinfo", fake_getaddrinfo
    )
    return addresses


@pytest.fixture
async def portal(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> dict[str, Any]:
    created = await create_client(client, super_admin_headers)
    key = await issue_api_key(client, super_admin_headers, created["id"])
    return {"id": created["id"], "key": {"X-API-Key": key["api_key"]}}


async def test_signing_secret_is_shown_only_once(
    client: AsyncClient, portal: dict[str, Any], public_dns: dict[str, str]
) -> None:
    created = await client.put(
        WEBHOOK_URL,
        json={"url": "https://hooks.example.com/everycred"},
        headers=portal["key"],
    )

    assert created.status_code == 200, created.text
    secret = created.json()["signing_secret"]
    assert secret.startswith("whsec_")
    fetched = await client.get(WEBHOOK_URL, headers=portal["key"])
    assert fetched.json()["signing_secret"] is None
    assert secret not in fetched.text

    unchanged = await client.put(
        WEBHOOK_URL,
        json={"url": "https://hooks.example.com/v2"},
        headers=portal["key"],
    )
    assert unchanged.json()["signing_secret"] is None
    rotated = await client.put(
        WEBHOOK_URL,
        json={"url": "https://hooks.example.com/v2", "rotate_secret": True},
        headers=portal["key"],
    )
    assert rotated.json()["signing_secret"] not in (None, secret)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://hooks.example.com/x", "https"),
        ("https://user:pw@hooks.example.com/x", "credentials"),
        ("https://internal.example.com/x", "resolve"),
    ],
)
async def test_unsafe_urls_are_refused(
    client: AsyncClient,
    portal: dict[str, Any],
    public_dns: dict[str, str],
    url: str,
    reason: str,
) -> None:
    response = await client.put(
        WEBHOOK_URL, json={"url": url}, headers=portal["key"]
    )

    assert response.status_code == 422
    assert reason in response.json()["error"]["message"]


async def test_host_resolving_to_private_address_is_refused(
    client: AsyncClient, portal: dict[str, Any], public_dns: dict[str, str]
) -> None:
    public_dns["sneaky.example.com"] = "10.0.0.5"

    response = await client.put(
        WEBHOOK_URL,
        json={"url": "https://sneaky.example.com/x"},
        headers=portal["key"],
    )

    assert response.status_code == 422
    assert "non-public" in response.json()["error"]["message"]


async def test_webhook_routes_require_api_key(client: AsyncClient) -> None:
    response = await client.get(WEBHOOK_URL)

    assert response.status_code == 401


def test_signature_round_trip_and_tamper_detection() -> None:
    body = webhook_delivery.encode_body({"event": "session.completed"})
    header = webhook_delivery.sign("whsec_x", int(time.time()), body)

    assert webhook_delivery.verify_signature("whsec_x", header, body)
    assert not webhook_delivery.verify_signature("whsec_y", header, body)
    assert not webhook_delivery.verify_signature(
        "whsec_x", header, body + b" "
    )
    assert not webhook_delivery.verify_signature(
        "whsec_x", header, body, now=time.time() + 3600
    )
    assert not webhook_delivery.verify_signature("whsec_x", "garbage", body)


def test_retry_delays_grow_then_level_off() -> None:
    delays = [retry_delay(attempt) for attempt in range(1, 9)]

    assert delays == sorted(delays)
    assert delays[-1] == delays[-2] == timedelta(hours=1)


async def test_failed_delivery_is_retried_then_abandoned(
    client: AsyncClient,
    portal: dict[str, Any],
    public_dns: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put(
        WEBHOOK_URL,
        json={"url": "https://hooks.example.com/everycred"},
        headers=portal["key"],
    )
    settings = get_settings().model_copy(update={"webhook_max_attempts": 2})
    endpoint = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )

    async def service(session: AsyncSession) -> WebhookService:
        return WebhookService(
            session=session,
            webhooks=ClientWebhookRepository(session),
            deliveries=WebhookDeliveryRepository(session),
            secret_store=build_secret_store(session, settings),
            http_client=endpoint,
            settings=settings,
        )

    async with session_factory() as session:
        webhooks = await service(session)
        await webhooks.enqueue(
            uuid.UUID(portal["id"]), "session.failed", {"session_id": "s"}
        )
        await session.commit()
        assert await webhooks.deliver_due() == 1

        delivery = await session.scalar(select(WebhookDelivery))
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == 1
        assert delivery.last_status_code == 503
        # Not due again until the backoff has passed.
        assert await webhooks.deliver_due() == 0
        delivery.next_attempt_at = utc_now()
        await session.commit()

        assert await webhooks.deliver_due() == 1
        await session.refresh(delivery)
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.attempts == 2


async def test_inactive_webhook_queues_nothing(
    client: AsyncClient,
    portal: dict[str, Any],
    public_dns: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put(
        WEBHOOK_URL,
        json={
            "url": "https://hooks.example.com/everycred",
            "is_active": False,
        },
        headers=portal["key"],
    )
    settings = get_settings()

    async with session_factory() as session:
        webhooks = WebhookService(
            session=session,
            webhooks=ClientWebhookRepository(session),
            deliveries=WebhookDeliveryRepository(session),
            secret_store=build_secret_store(session, settings),
            http_client=httpx.AsyncClient(),
            settings=settings,
        )
        await webhooks.enqueue(uuid.UUID(portal["id"]), "session.failed", {})
        await session.commit()
        assert await session.scalar(select(WebhookDelivery)) is None


async def test_delete_webhook(
    client: AsyncClient, portal: dict[str, Any], public_dns: dict[str, str]
) -> None:
    await client.put(
        WEBHOOK_URL,
        json={"url": "https://hooks.example.com/everycred"},
        headers=portal["key"],
    )

    deleted = await client.delete(WEBHOOK_URL, headers=portal["key"])
    missing = await client.get(WEBHOOK_URL, headers=portal["key"])

    assert deleted.status_code == 204
    assert missing.status_code == 404
