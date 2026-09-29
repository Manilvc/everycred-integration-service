import pytest
from httpx import AsyncClient

from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.integration_tools.conftest import (
    CLIENT_TOOLS_URL,
    TOOLS_URL,
)

pytestmark = pytest.mark.usefixtures("tool_catalogue")


def codes(response) -> list[str]:
    return [tool["code"] for tool in response.json()["items"]]


async def test_admin_listing_requires_super_admin(client: AsyncClient) -> None:
    response = await client.get(TOOLS_URL)

    assert response.status_code == 401


async def test_admin_lists_active_tools_in_display_order(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(TOOLS_URL, headers=super_admin_headers)

    assert response.status_code == 200
    assert codes(response) == ["acme-verify", "globex-gather", "umbrella-rec"]
    assert response.json()["total"] == 3


async def test_admin_can_include_inactive_tools(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        TOOLS_URL,
        params={"include_inactive": "true"},
        headers=super_admin_headers,
    )

    assert "initech-legacy" in codes(response)
    assert response.json()["total"] == 4


async def test_admin_can_filter_by_integration_type(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        TOOLS_URL,
        params={"integration_type": "gather"},
        headers=super_admin_headers,
    )

    assert codes(response) == ["acme-verify", "globex-gather"]


async def test_tool_lists_every_type_it_serves(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(TOOLS_URL, headers=super_admin_headers)

    acme = response.json()["items"][0]
    assert [t["code"] for t in acme["integration_types"]] == [
        "confirm",
        "gather",
    ]
    assert acme["provider"] == "Acme"


async def test_connector_parameters_come_from_its_signature(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(TOOLS_URL, headers=super_admin_headers)
    acme, globex = response.json()["items"][:2]

    assert acme["connector"]["is_available"] is True
    assert acme["connector"]["kind"] == "code"
    assert acme["connector"]["parameters"] == [
        {
            "name": "api_token",
            "kind": "positional_or_keyword",
            "required": True,
            "annotation": "str",
        },
        {
            "name": "region",
            "kind": "keyword_only",
            "required": False,
            "annotation": "str",
        },
        {
            "name": "extra",
            "kind": "var_keyword",
            "required": False,
            "annotation": "object",
        },
    ]
    assert globex["connector"] == {
        "is_available": False,
        "kind": None,
        "parameters": [],
        "operations": [],
        "flows": [],
    }


async def test_connector_defaults_are_not_published(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(TOOLS_URL, headers=super_admin_headers)

    assert '"in"' not in response.text


async def test_client_listing_requires_api_key(client: AsyncClient) -> None:
    response = await client.get(CLIENT_TOOLS_URL)

    assert response.status_code == 401


async def test_client_sees_only_active_tools_of_enabled_types(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    await client.put(
        f"/api/v1/clients/{portal['id']}/integrations/confirm",
        json={},
        headers=super_admin_headers,
    )
    key = await issue_api_key(client, super_admin_headers, portal["id"])

    response = await client.get(
        CLIENT_TOOLS_URL, headers={"X-API-Key": key["api_key"]}
    )

    # initech-legacy serves confirm but is inactive; the others serve
    # types this client has not enabled.
    assert codes(response) == ["acme-verify"]
    # Only the client's enabled types are shown, not "gather".
    assert [
        t["code"] for t in response.json()["items"][0]["integration_types"]
    ] == ["confirm"]


async def test_client_without_enabled_types_sees_no_tools(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    key = await issue_api_key(client, super_admin_headers, portal["id"])

    response = await client.get(
        CLIENT_TOOLS_URL, headers={"X-API-Key": key["api_key"]}
    )

    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_client_cannot_use_include_inactive(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    portal = await create_client(client, super_admin_headers)
    key = await issue_api_key(client, super_admin_headers, portal["id"])

    response = await client.get(
        CLIENT_TOOLS_URL,
        params={"include_inactive": "true"},
        headers={"X-API-Key": key["api_key"]},
    )

    assert response.status_code == 422
