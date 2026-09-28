import uuid

import pytest
from httpx import AsyncClient

from tests.features.clients.conftest import CLIENTS_URL, create_client


async def test_client_management_requires_super_admin(
    client: AsyncClient,
) -> None:
    response = await client.post(
        CLIENTS_URL, json={"code": "issuer-portal", "name": "Issuer Portal"}
    )

    assert response.status_code == 401


async def test_create_client(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    created = await create_client(client, super_admin_headers)

    assert created["code"] == "issuer-portal"
    assert created["name"] == "Issuer Portal"
    assert created["is_active"] is True


async def test_duplicate_client_code_is_rejected(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    await create_client(client, super_admin_headers)

    response = await client.post(
        CLIENTS_URL,
        json={"code": "issuer-portal", "name": "Another"},
        headers=super_admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "client_code_taken"


@pytest.mark.parametrize(
    "code", ["Issuer-Portal", "has space", "-leading", "a", "double--dash"]
)
async def test_invalid_client_code_is_rejected(
    client: AsyncClient, super_admin_headers: dict[str, str], code: str
) -> None:
    response = await client.post(
        CLIENTS_URL,
        json={"code": code, "name": "Portal"},
        headers=super_admin_headers,
    )

    assert response.status_code == 422


async def test_list_clients_is_paginated_and_sorted_by_name(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    for code in ("wallet", "admin-console", "verifier"):
        await create_client(client, super_admin_headers, code=code)

    response = await client.get(
        CLIENTS_URL, params={"limit": 2}, headers=super_admin_headers
    )

    body = response.json()
    assert [item["code"] for item in body["items"]] == [
        "admin-console",
        "verifier",
    ]
    assert body["total"] == 3


async def test_get_unknown_client_returns_404(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{CLIENTS_URL}/{uuid.uuid4()}", headers=super_admin_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "client_not_found"
