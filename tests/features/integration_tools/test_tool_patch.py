import base64
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.media import LogoImage, MediaStoreError, get_media_store
from tests.features.integration_tools.conftest import TOOLS_URL

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
TOOL_URL = f"{TOOLS_URL}/acme-http"
CONFIG: dict[str, Any] = {
    "type": "http",
    "environments": {"sandbox": "https://api.acme.example.com"},
    "default_environment": "sandbox",
    "headers": {"Authorization": "Bearer {credentials.api_token}"},
    "operations": {
        "lookup": {"method": "GET", "path": "/people/{kwargs.person_id}"}
    },
    "flows": {
        "profile": {
            "purpose": "gather",
            "steps": [{"operation": "lookup", "inputs": ["person_id"]}],
            "outputs": {"full_name": "name"},
        }
    },
}
DEFINITION: dict[str, Any] = {
    "name": "Acme Verify",
    "provider": "Acme",
    "description": "Identity checks.",
    "display_order": 5,
    "integration_types": ["confirm"],
    "connector_config": CONFIG,
    "fields": [{"flow": "profile", "key": "full_name", "label": "Name"}],
}


class FakeMediaStore:
    def __init__(self) -> None:
        self.objects: dict[str, LogoImage] = {}
        self.fail_saves = False

    async def save(self, key: str, image: LogoImage) -> None:
        if self.fail_saves:
            raise MediaStoreError
        self.objects[key] = image

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def media(app: FastAPI) -> FakeMediaStore:
    store = FakeMediaStore()
    app.dependency_overrides[get_media_store] = lambda: store
    return store


@pytest.fixture
async def saved_tool(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> dict[str, Any]:
    response = await client.put(
        TOOL_URL, json=DEFINITION, headers=super_admin_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def global_field_keys(
    client: AsyncClient, headers: dict[str, str]
) -> list[str]:
    response = await client.get(f"{TOOL_URL}/fields", headers=headers)
    assert response.status_code == 200, response.text
    return [field["key"] for field in response.json()["items"]]


async def test_patch_logo_keeps_everything_else(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
    media: FakeMediaStore,
) -> None:
    logo = "data:image/png;base64," + base64.b64encode(PNG).decode()

    response = await client.patch(
        TOOL_URL, json={"logo": logo}, headers=super_admin_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["logo_url"].endswith(".png")
    for unchanged in ("name", "provider", "description", "display_order"):
        assert body[unchanged] == saved_tool[unchanged]
    assert body["connector"] == saved_tool["connector"]
    assert [flow["name"] for flow in body["connector"]["flows"]] == ["profile"]
    assert await global_field_keys(client, super_admin_headers) == [
        "full_name"
    ]
    assert len(media.objects) == 1


async def test_patch_changes_only_the_sent_columns(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
) -> None:
    response = await client.patch(
        TOOL_URL,
        json={"is_active": False, "description": None},
        headers=super_admin_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is False
    assert body["description"] is None
    assert body["name"] == saved_tool["name"]
    assert body["connector"] == saved_tool["connector"]


async def test_patch_null_logo_removes_it(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
    media: FakeMediaStore,
) -> None:
    logo = base64.b64encode(PNG).decode()
    await client.patch(
        TOOL_URL, json={"logo": logo}, headers=super_admin_headers
    )

    response = await client.patch(
        TOOL_URL, json={"logo": None}, headers=super_admin_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["logo_url"] is None
    assert media.objects == {}


async def test_patch_replaces_types_and_fields(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
) -> None:
    response = await client.patch(
        TOOL_URL,
        json={
            "integration_types": ["gather"],
            "fields": [{"flow": "profile", "key": "email"}],
        },
        headers=super_admin_headers,
    )

    assert response.status_code == 200, response.text
    codes = [item["code"] for item in response.json()["integration_types"]]
    assert codes == ["gather"]
    assert await global_field_keys(client, super_admin_headers) == ["email"]


async def test_patch_checks_fields_against_the_stored_flows(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
) -> None:
    response = await client.patch(
        TOOL_URL,
        json={"fields": [{"flow": "missing", "key": "email"}]},
        headers=super_admin_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": None},
        {"integration_types": None},
        {"fields": None},
        {"unknown": 1},
        {"logo": base64.b64encode(b"GIF89a" + b"\x00" * 16).decode()},
    ],
)
async def test_patch_rejects_invalid_changes(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
    body: dict[str, Any],
) -> None:
    response = await client.patch(
        TOOL_URL, json=body, headers=super_admin_headers
    )

    assert response.status_code == 422


async def test_patch_rejects_unknown_type(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
) -> None:
    response = await client.patch(
        TOOL_URL,
        json={"integration_types": ["nope"]},
        headers=super_admin_headers,
    )

    assert response.status_code == 422


async def test_patch_unknown_tool_is_404(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
) -> None:
    response = await client.patch(
        f"{TOOLS_URL}/does-not-exist",
        json={"is_active": False},
        headers=super_admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"


async def test_patch_keeps_tool_when_logo_store_fails(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    saved_tool: dict[str, Any],
    media: FakeMediaStore,
) -> None:
    media.fail_saves = True

    response = await client.patch(
        TOOL_URL,
        json={"name": "Renamed", "logo": base64.b64encode(PNG).decode()},
        headers=super_admin_headers,
    )

    assert response.status_code == 503
    listed = await client.get(TOOLS_URL, headers=super_admin_headers)
    names = [tool["name"] for tool in listed.json()["items"]]
    assert "Renamed" not in names


async def test_patch_requires_super_admin(
    client: AsyncClient, tool_catalogue: None
) -> None:
    response = await client.patch(TOOL_URL, json={"is_active": False})

    assert response.status_code == 401
