import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.clients.models import Client, ClientApiKey
from tests.features.clients.conftest import (
    CLIENTS_URL,
    OWN_CONFIGURATION_URL,
    create_client,
    issue_api_key,
)


async def test_issued_key_is_returned_once_with_expected_format(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)

    response = await client.post(
        f"{CLIENTS_URL}/{portal['id']}/api-keys",
        json={"name": "production"},
        headers=super_admin_headers,
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    scheme, prefix, secret = body["api_key"].split("_", 2)
    assert scheme == "eci"
    assert prefix == body["key_prefix"]
    assert len(secret) >= 43


async def test_key_listing_never_exposes_key_or_hash(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])

    response = await client.get(
        f"{CLIENTS_URL}/{portal['id']}/api-keys", headers=super_admin_headers
    )

    assert response.status_code == 200
    assert issued["api_key"] not in response.text
    assert "key_hash" not in response.text
    assert response.json()[0]["key_prefix"] == issued["key_prefix"]


async def test_key_is_stored_only_as_hash(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])

    async with session_factory() as session:
        stored = await session.get(ClientApiKey, uuid.UUID(issued["id"]))

    assert stored is not None
    assert stored.key_hash != issued["api_key"]
    assert issued["api_key"] not in stored.key_hash
    assert len(stored.key_hash) == 64


async def test_valid_key_authenticates_its_client(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])

    response = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )

    assert response.status_code == 200
    assert response.json()["client"]["code"] == "issuer-portal"


async def test_last_used_at_is_recorded(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])

    await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )
    keys = await client.get(
        f"{CLIENTS_URL}/{portal['id']}/api-keys", headers=super_admin_headers
    )

    assert keys.json()[0]["last_used_at"] is not None


async def test_missing_or_bad_keys_are_rejected(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    tampered_key = issued["api_key"][:-4] + "abcd"

    responses = [
        await client.get(OWN_CONFIGURATION_URL),
        await client.get(
            OWN_CONFIGURATION_URL, headers={"X-API-Key": "not-a-key"}
        ),
        await client.get(
            OWN_CONFIGURATION_URL, headers={"X-API-Key": tampered_key}
        ),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


async def test_super_admin_token_is_not_an_api_key(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        OWN_CONFIGURATION_URL, headers=super_admin_headers
    )

    assert response.status_code == 401


async def test_revoked_key_stops_working(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    revoke_url = f"{CLIENTS_URL}/{portal['id']}/api-keys/{issued['id']}/revoke"

    first_revoke = await client.post(revoke_url, headers=super_admin_headers)
    second_revoke = await client.post(revoke_url, headers=super_admin_headers)
    response = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )

    assert first_revoke.json()["revoked_at"] is not None
    assert (
        second_revoke.json()["revoked_at"] == first_revoke.json()["revoked_at"]
    )
    assert response.status_code == 401


async def test_revoking_another_clients_key_returns_404(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    wallet = await create_client(client, super_admin_headers, code="wallet")
    wallet_key = await issue_api_key(client, super_admin_headers, wallet["id"])

    response = await client.post(
        f"{CLIENTS_URL}/{portal['id']}/api-keys/{wallet_key['id']}/revoke",
        headers=super_admin_headers,
    )

    assert response.status_code == 404


async def test_expired_key_is_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(
        client,
        super_admin_headers,
        portal["id"],
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    async with session_factory() as session:
        await session.execute(
            update(ClientApiKey)
            .where(ClientApiKey.id == uuid.UUID(issued["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    response = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )

    assert response.status_code == 401


async def test_expiry_in_the_past_is_rejected_at_issue(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)

    response = await client.post(
        f"{CLIENTS_URL}/{portal['id']}/api-keys",
        json={
            "name": "old",
            "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        headers=super_admin_headers,
    )

    assert response.status_code == 422


async def test_key_of_inactive_client_is_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    async with session_factory() as session:
        await session.execute(
            update(Client)
            .where(Client.id == uuid.UUID(portal["id"]))
            .values(is_active=False)
        )
        await session.commit()

    response = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )

    assert response.status_code == 401
