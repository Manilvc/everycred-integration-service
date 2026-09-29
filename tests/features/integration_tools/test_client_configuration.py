import pytest
from httpx import AsyncClient

from tests.features.clients.conftest import create_client, issue_api_key

pytestmark = pytest.mark.usefixtures("tool_catalogue")


@pytest.fixture
async def portal(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> dict:
    created = await create_client(client, super_admin_headers)
    key = await issue_api_key(client, super_admin_headers, created["id"])
    return {**created, "headers": {"X-API-Key": key["api_key"]}}


async def set_config(
    client: AsyncClient,
    headers: dict[str, str],
    client_id: str,
    type_code: str,
    **body: object,
):
    return await client.put(
        f"/api/v1/clients/{client_id}/integrations/{type_code}",
        json=body,
        headers=headers,
    )


async def test_admin_chooses_tool_for_a_type(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    response = await set_config(
        client,
        super_admin_headers,
        portal["id"],
        "gather",
        tool_code="acme-verify",
    )

    assert response.status_code == 200
    assert response.json()["integration_tool"] == {
        "code": "acme-verify",
        "name": "Acme Verify",
        "provider": "Acme",
    }


async def test_same_tool_can_serve_two_types_for_one_client(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    for type_code in ("confirm", "gather"):
        response = await set_config(
            client,
            super_admin_headers,
            portal["id"],
            type_code,
            tool_code="acme-verify",
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("tool_code", "status_code", "error_code"),
    [
        ("no-such-tool", 404, "integration_tool_not_found"),
        ("umbrella-rec", 422, "integration_tool_not_usable"),
        ("initech-legacy", 422, "integration_tool_not_usable"),
    ],
)
async def test_invalid_tool_choices_are_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    portal: dict,
    tool_code: str,
    status_code: int,
    error_code: str,
) -> None:
    response = await set_config(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        tool_code=tool_code,
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code


async def test_sending_null_tool_clears_the_choice(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    await set_config(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        tool_code="acme-verify",
    )

    response = await set_config(
        client, super_admin_headers, portal["id"], "confirm", tool_code=None
    )

    assert response.json()["integration_tool"] is None


async def test_client_configuration_shows_chosen_tool(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    await set_config(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        tool_code="acme-verify",
    )

    response = await client.get(
        "/api/v1/client/configuration", headers=portal["headers"]
    )

    integration = response.json()["integrations"][0]
    assert integration["integration_tool"]["code"] == "acme-verify"


async def test_client_updates_its_own_settings(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    await set_config(
        client,
        super_admin_headers,
        portal["id"],
        "confirm",
        tool_code="acme-verify",
        settings={"callback_url": "old"},
    )

    response = await client.put(
        "/api/v1/client/integrations/confirm/settings",
        json={"settings": {"callback_url": "https://portal.example.com"}},
        headers=portal["headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["settings"] == {"callback_url": "https://portal.example.com"}
    # The client's edit leaves the admin's choices untouched.
    assert body["integration_tool"]["code"] == "acme-verify"
    assert body["is_enabled"] is True
    admin_view = await client.get(
        f"/api/v1/clients/{portal['id']}/integrations",
        headers=super_admin_headers,
    )
    assert admin_view.json()[0]["settings"] == body["settings"]


@pytest.mark.parametrize(
    "extra_field", [{"is_enabled": False}, {"tool_code": "globex-gather"}]
)
async def test_client_cannot_change_enablement_or_tool(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    portal: dict,
    extra_field: dict,
) -> None:
    await set_config(client, super_admin_headers, portal["id"], "gather")

    response = await client.put(
        "/api/v1/client/integrations/gather/settings",
        json={"settings": {}, **extra_field},
        headers=portal["headers"],
    )

    assert response.status_code == 422


async def test_client_cannot_edit_settings_of_type_not_enabled(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    await set_config(
        client, super_admin_headers, portal["id"], "gather", is_enabled=False
    )

    disabled = await client.put(
        "/api/v1/client/integrations/gather/settings",
        json={"settings": {}},
        headers=portal["headers"],
    )
    never_configured = await client.put(
        "/api/v1/client/integrations/records/settings",
        json={"settings": {}},
        headers=portal["headers"],
    )
    unknown = await client.put(
        "/api/v1/client/integrations/nope/settings",
        json={"settings": {}},
        headers=portal["headers"],
    )

    assert disabled.status_code == 403
    assert never_configured.status_code == 403
    assert disabled.json()["error"]["code"] == "integration_not_enabled"
    assert unknown.status_code == 404


async def test_client_settings_update_requires_api_key(
    client: AsyncClient,
) -> None:
    response = await client.put(
        "/api/v1/client/integrations/confirm/settings",
        json={"settings": {}},
    )

    assert response.status_code == 401


async def test_client_settings_size_is_limited(
    client: AsyncClient, super_admin_headers: dict[str, str], portal: dict
) -> None:
    await set_config(client, super_admin_headers, portal["id"], "confirm")

    response = await client.put(
        "/api/v1/client/integrations/confirm/settings",
        json={"settings": {"blob": "x" * 20_000}},
        headers=portal["headers"],
    )

    assert response.status_code == 422
