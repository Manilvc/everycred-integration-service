import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.secret_store import LocalSecret
from app.features.user_connections.models import UserIntegrationConnection
from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.user_connections.conftest import (
    USER_UUID,
    connection_url,
    enable_integrations,
)

SECRET_TOKEN = "tok_live_do_not_leak"


async def test_connection_routes_require_api_key(client: AsyncClient) -> None:
    response = await client.get(connection_url())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_saving_parameters_hides_values_in_response(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("confirm"),
        json={"args": [SECRET_TOKEN], "kwargs": {"region": "eu"}},
        headers=client_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["user_uuid"] == USER_UUID
    assert body["parameters"] == {"arg_count": 1, "kwarg_names": ["region"]}
    assert SECRET_TOKEN not in response.text
    assert "eu" not in response.text


async def test_parameters_are_kept_in_the_secret_store(
    client: AsyncClient,
    client_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put(
        connection_url("confirm"),
        json={"args": [SECRET_TOKEN]},
        headers=client_headers,
    )

    async with session_factory() as session:
        stored = await session.scalar(select(UserIntegrationConnection))
        secret = await session.scalar(select(LocalSecret))

    assert stored is not None
    assert secret is not None
    # The connection row holds only a reference to the secret...
    assert stored.parameters_secret_reference == f"local:{secret.id}"
    assert stored.parameter_summary == {"arg_count": 1, "kwarg_names": []}
    # ...and the secret store keeps the value encrypted.
    assert SECRET_TOKEN not in secret.ciphertext


async def test_parameters_that_do_not_fit_connector_are_rejected(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("confirm"),
        json={"kwargs": {"region": "eu"}},
        headers=client_headers,
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "invalid_connection_parameters"
    assert "api_token" in body["message"]


async def test_type_without_connector_still_accepts_parameters(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("unwired"),
        json={"args": [1, 2], "kwargs": {"anything": True}},
        headers=client_headers,
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "kwargs",
    [
        {"not valid": 1},
        {"class": 1},
        {"_private": 1},
        {"1st": 1},
        {"operation": 1},
        {"self": 1},
    ],
)
async def test_invalid_kwarg_names_are_rejected(
    client: AsyncClient,
    client_headers: dict[str, str],
    kwargs: dict[str, int],
) -> None:
    response = await client.put(
        connection_url("unwired"),
        json={"kwargs": kwargs},
        headers=client_headers,
    )

    assert response.status_code == 422


async def test_integration_not_enabled_for_client_is_forbidden(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("not-enabled"),
        json={"args": ["x"]},
        headers=client_headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "integration_not_enabled"


async def test_unknown_integration_type_returns_404(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("no-such-type"), json={}, headers=client_headers
    )

    assert response.status_code == 404


async def test_invalid_user_uuid_is_rejected(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.get(
        connection_url(user_uuid="not-a-uuid"), headers=client_headers
    )

    assert response.status_code == 422


async def test_list_get_and_delete_connection(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    await client.put(
        connection_url("confirm"), json={"args": ["t"]}, headers=client_headers
    )
    await client.put(
        connection_url("unwired"), json={}, headers=client_headers
    )

    listing = await client.get(connection_url(), headers=client_headers)
    single = await client.get(
        connection_url("confirm"), headers=client_headers
    )
    deleted = await client.delete(
        connection_url("confirm"), headers=client_headers
    )
    after_delete = await client.get(
        connection_url("confirm"), headers=client_headers
    )

    assert [c["integration_type_code"] for c in listing.json()] == [
        "confirm",
        "unwired",
    ]
    assert single.json()["integration_type_code"] == "confirm"
    assert deleted.status_code == 204
    assert after_delete.status_code == 404
    assert after_delete.json()["error"]["code"] == "connection_not_found"


async def test_clients_cannot_see_each_others_users(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    client_headers: dict[str, str],
) -> None:
    await client.put(
        connection_url("confirm"), json={"args": ["t"]}, headers=client_headers
    )
    wallet = await create_client(client, super_admin_headers, code="wallet")
    await enable_integrations(
        client, super_admin_headers, wallet["id"], ["confirm"]
    )
    wallet_key = await issue_api_key(client, super_admin_headers, wallet["id"])
    wallet_headers = {"X-API-Key": wallet_key["api_key"]}

    listing = await client.get(connection_url(), headers=wallet_headers)
    single = await client.get(
        connection_url("confirm"), headers=wallet_headers
    )

    assert listing.json() == []
    assert single.status_code == 404


async def test_different_users_of_one_client_are_separate(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    other_user = str(uuid.uuid4())
    await client.put(
        connection_url("confirm"), json={"args": ["t"]}, headers=client_headers
    )

    response = await client.get(
        connection_url(user_uuid=other_user), headers=client_headers
    )

    assert response.json() == []
