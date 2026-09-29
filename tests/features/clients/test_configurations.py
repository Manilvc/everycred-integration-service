from httpx import AsyncClient

from tests.features.clients.conftest import (
    CLIENTS_URL,
    OWN_CONFIGURATION_URL,
    create_client,
    issue_api_key,
)


async def set_integration(
    client: AsyncClient,
    headers: dict[str, str],
    client_id: str,
    type_code: str,
    **body: object,
):
    return await client.put(
        f"{CLIENTS_URL}/{client_id}/integrations/{type_code}",
        json=body,
        headers=headers,
    )


async def test_setting_integration_creates_then_replaces_config(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_types: None,
) -> None:
    portal = await create_client(client, super_admin_headers)

    created = await set_integration(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        settings={"callback_url": "https://portal.example.com/hook"},
    )
    replaced = await set_integration(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        is_enabled=False,
        settings={"region": "in"},
    )

    assert created.status_code == 200
    assert replaced.json()["is_enabled"] is False
    assert replaced.json()["settings"] == {"region": "in"}
    listing = await client.get(
        f"{CLIENTS_URL}/{portal['id']}/integrations",
        headers=super_admin_headers,
    )
    assert len(listing.json()) == 1


async def test_unknown_integration_type_returns_404(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_types: None,
) -> None:
    portal = await create_client(client, super_admin_headers)

    response = await set_integration(
        client, super_admin_headers, portal["id"], "does-not-exist"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_type_not_found"


async def test_oversized_settings_are_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_types: None,
) -> None:
    portal = await create_client(client, super_admin_headers)

    response = await set_integration(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        settings={"blob": "x" * 20_000},
    )

    assert response.status_code == 422


async def test_client_sees_only_enabled_configs_of_active_types(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_types: None,
) -> None:
    portal = await create_client(client, super_admin_headers)
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    await set_integration(
        client,
        super_admin_headers,
        portal["id"],
        "gather",
        settings={"fields": ["name", "dob"]},
    )
    await set_integration(
        client, super_admin_headers, portal["id"], "confirm", is_enabled=False
    )
    await set_integration(client, super_admin_headers, portal["id"], "retired")

    response = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": issued["api_key"]}
    )

    body = response.json()
    assert body["client"] == {
        "id": portal["id"],
        "code": "issuer-portal",
        "name": "Issuer Portal",
    }
    assert [
        integration["integration_type_code"]
        for integration in body["integrations"]
    ] == ["gather"]
    assert body["integrations"][0]["settings"] == {"fields": ["name", "dob"]}


async def test_each_key_returns_only_its_own_clients_config(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_types: None,
) -> None:
    portal = await create_client(client, super_admin_headers)
    wallet = await create_client(client, super_admin_headers, code="wallet")
    portal_key = await issue_api_key(client, super_admin_headers, portal["id"])
    wallet_key = await issue_api_key(client, super_admin_headers, wallet["id"])
    await set_integration(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        settings={"owner": "portal"},
    )
    await set_integration(
        client,
        super_admin_headers,
        wallet["id"],
        "gather",
        settings={"owner": "wallet"},
    )

    portal_view = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": portal_key["api_key"]}
    )
    wallet_view = await client.get(
        OWN_CONFIGURATION_URL, headers={"X-API-Key": wallet_key["api_key"]}
    )

    assert portal_view.json()["client"]["code"] == "issuer-portal"
    assert [i["settings"] for i in portal_view.json()["integrations"]] == [
        {"owner": "portal"}
    ]
    assert wallet_view.json()["client"]["code"] == "wallet"
    assert [i["settings"] for i in wallet_view.json()["integrations"]] == [
        {"owner": "wallet"}
    ]
