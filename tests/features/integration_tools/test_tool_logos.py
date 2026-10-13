import base64
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.media import (
    InvalidLogoError,
    LocalMediaStore,
    LogoImage,
    MediaStoreError,
    decode_logo,
    get_media_store,
)
from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.integration_tools.conftest import (
    CLIENT_TOOLS_URL,
    TOOLS_URL,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
BASE_URL = "http://127.0.0.1:8001/media"


def b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


class FakeMediaStore:
    """Keeps logos in memory; can be told to fail."""

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


def definition(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Acme Verify",
        "provider": "Acme",
        "integration_types": ["confirm"],
    }
    body.update(changes)
    return body


async def save_tool(
    client: AsyncClient, headers: dict[str, str], **changes: Any
) -> Any:
    return await client.put(
        f"{TOOLS_URL}/acme-verify",
        json=definition(**changes),
        headers=headers,
    )


async def test_logo_is_stored_and_listed_as_a_full_link(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> None:
    saved = await save_tool(client, super_admin_headers, logo=b64(PNG))

    assert saved.status_code == 200, saved.text
    (key,) = media.objects
    assert key.startswith("integration-tools/logos/acme-verify/")
    assert key.endswith(".png")
    assert media.objects[key].content == PNG
    assert media.objects[key].content_type == "image/png"
    assert saved.json()["logo_url"] == f"{BASE_URL}/{key}"
    listed = await client.get(TOOLS_URL, headers=super_admin_headers)
    acme = next(
        tool
        for tool in listed.json()["items"]
        if tool["code"] == "acme-verify"
    )
    assert acme["logo_url"] == f"{BASE_URL}/{key}"


@pytest.mark.parametrize(
    ("logo", "extension"),
    [
        (f"data:image/jpeg;base64,{b64(JPEG)}", "jpg"),
        (b64(WEBP), "webp"),
    ],
)
async def test_jpeg_webp_and_data_uris_are_accepted(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
    logo: str,
    extension: str,
) -> None:
    saved = await save_tool(client, super_admin_headers, logo=logo)

    assert saved.status_code == 200, saved.text
    (key,) = media.objects
    assert key.endswith(f".{extension}")


async def test_leaving_logo_out_keeps_it_and_null_removes_it(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> None:
    first = await save_tool(client, super_admin_headers, logo=b64(PNG))
    kept = await save_tool(client, super_admin_headers, name="Acme Verify 2")
    removed = await save_tool(client, super_admin_headers, logo=None)

    assert kept.json()["logo_url"] == first.json()["logo_url"]
    assert removed.json()["logo_url"] is None
    assert media.objects == {}


async def test_new_logo_replaces_and_deletes_the_old_file(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> None:
    first = await save_tool(client, super_admin_headers, logo=b64(PNG))
    second = await save_tool(client, super_admin_headers, logo=b64(JPEG))

    assert second.json()["logo_url"] != first.json()["logo_url"]
    (key,) = media.objects
    assert key.endswith(".jpg")


@pytest.mark.parametrize(
    "logo",
    [
        "not base64 !!!",
        b64(b"GIF89a" + b"\x00" * 20),
        b64(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
        f"data:image/svg+xml;base64,{b64(PNG)}",
        "",
    ],
)
async def test_invalid_logos_are_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
    logo: str,
) -> None:
    response = await save_tool(client, super_admin_headers, logo=logo)

    assert response.status_code == 422
    assert media.objects == {}


async def test_store_failure_leaves_the_tool_unchanged(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> None:
    media.fail_saves = True

    response = await save_tool(
        client, super_admin_headers, name="Changed", logo=b64(PNG)
    )
    listed = await client.get(TOOLS_URL, headers=super_admin_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "media_store_unavailable"
    acme = next(
        tool
        for tool in listed.json()["items"]
        if tool["code"] == "acme-verify"
    )
    assert acme["name"] == "Acme Verify"
    assert acme["logo_url"] is None


async def test_client_listings_show_the_logo_link(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    tool_catalogue: None,
    media: FakeMediaStore,
) -> None:
    saved = await save_tool(client, super_admin_headers, logo=b64(PNG))
    logo_url = saved.json()["logo_url"]
    portal = await create_client(client, super_admin_headers)
    await client.put(
        f"/v1/clients/{portal['id']}/integrations/confirm",
        json={"tool_code": "acme-verify"},
        headers=super_admin_headers,
    )
    key = {
        "X-API-Key": (
            await issue_api_key(client, super_admin_headers, portal["id"])
        )["api_key"]
    }
    user = str(uuid.uuid4())

    tools = await client.get(CLIENT_TOOLS_URL, headers=key)
    screen = await client.get("/v1/client/integrations", headers=key)
    drawer = await client.get(
        "/v1/client/integrations/acme-verify", headers=key
    )
    user_view = await client.get(
        f"/v1/client/users/{user}/integrations",
        params={"status": "not_connected"},
        headers=key,
    )
    admin_view = await client.get(
        f"/v1/clients/{portal['id']}/integrations", headers=super_admin_headers
    )

    assert tools.json()["items"][0]["logo_url"] == logo_url
    confirm = next(
        group
        for group in screen.json()["data"]["groups"]
        if group["key"] == "confirm"
    )
    assert confirm["systems"][0]["logo_url"] == logo_url
    assert drawer.json()["logo_url"] == logo_url
    user_confirm = next(
        group
        for group in user_view.json()["data"]["groups"]
        if group["key"] == "confirm"
    )
    assert user_confirm["systems"][0]["logo_url"] == logo_url
    (config,) = admin_view.json()
    assert config["integration_tool"]["logo_url"] == logo_url


def test_decode_logo_identifies_by_signature_not_by_claim() -> None:
    assert decode_logo(b64(PNG)).content_type == "image/png"
    assert decode_logo(f"data:image/png;base64,{b64(JPEG)}").extension == "jpg"
    with pytest.raises(InvalidLogoError):
        decode_logo(b64(b"\x00" * 10))


def test_oversized_logo_is_rejected() -> None:
    too_big = PNG + b"\x00" * (1024 * 1024)

    with pytest.raises(InvalidLogoError, match="1 MB"):
        decode_logo(b64(too_big))


async def test_local_store_writes_and_deletes_inside_its_folder(
    tmp_path: Path,
) -> None:
    store = LocalMediaStore(str(tmp_path))
    image = LogoImage(PNG, "image/png", "png")

    await store.save("integration-tools/logos/acme/a.png", image)
    written = tmp_path / "integration-tools/logos/acme/a.png"
    assert written.read_bytes() == PNG
    await store.delete("integration-tools/logos/acme/a.png")
    assert not written.exists()
    with pytest.raises(MediaStoreError):
        await store.save("../escape.png", image)
