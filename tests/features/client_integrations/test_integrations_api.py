from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.http import oauth
from app.core.http_client import get_http_client
from app.core.secret_store import LocalSecret
from app.features.clients.models import ClientToolCredential
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_types.models import IntegrationType
from tests.features.clients.conftest import create_client, issue_api_key

LIST_URL = "/api/v1/client/integrations"
SECRET = "entra-client-secret-value"

ENTRA_CONFIG = {
    "type": "http",
    "environments": {"prod": "https://graph.example.com/v1.0"},
    "default_environment": "prod",
    "auth": {
        "type": "oauth2_client_credentials",
        "token_url": (
            "https://login.example.com/{credentials.tenant_id}/oauth2/token"
        ),
        "scope": "https://graph.example.com/.default",
    },
    "operations": {"organisation": {"method": "GET", "path": "/organization"}},
    "test_operation": "organisation",
}
IDME_CONFIG = {
    "type": "http",
    "environments": {"sandbox": "https://api.idme.example.com"},
    "default_environment": "sandbox",
    "headers": {"X-Api-Key": "{credentials.api_key}"},
    "operations": {
        "verify": {"path": "/verify", "body": {"id": "{kwargs.id}"}}
    },
}


class FakeProviders:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reject_token = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.example.com":
            if self.reject_token:
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(
                200, json={"access_token": "tok", "expires_in": 3600}
            )
        return httpx.Response(200, json={"success": True, "data": {}})


@pytest.fixture
def providers(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> FakeProviders:
    monkeypatch.setattr(oauth, "token_cache", oauth.TokenCache())
    fake = FakeProviders()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_http_client] = lambda: mock_client
    return fake


@pytest.fixture
async def setup(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    providers: FakeProviders,
) -> dict:
    async with session_factory() as session:
        confirm = IntegrationType(
            code="confirm",
            name="Confirm",
            description="Is this really the person?",
            display_order=1,
        )
        gather = IntegrationType(code="gather", name="Gather", display_order=2)
        session.add_all(
            [
                IntegrationTool(
                    code="entra",
                    name="Microsoft Entra ID",
                    display_order=1,
                    connector_config=ENTRA_CONFIG,
                    integration_types=[confirm],
                ),
                IntegrationTool(
                    code="idme",
                    name="ID.me",
                    display_order=2,
                    connector_config=IDME_CONFIG,
                    integration_types=[confirm],
                ),
                IntegrationTool(
                    code="login-gov",
                    name="Login.gov",
                    display_order=3,
                    integration_types=[confirm],
                ),
                IntegrationTool(
                    code="retired",
                    name="Retired",
                    is_active=False,
                    integration_types=[confirm],
                ),
                IntegrationTool(
                    code="workday",
                    name="Workday HRMS",
                    connector_config=IDME_CONFIG,
                    integration_types=[gather],
                ),
            ]
        )
        await session.commit()

    portal = await create_client(client, super_admin_headers)
    response = await client.put(
        f"/api/v1/clients/{portal['id']}/integrations/confirm",
        json={"tool_code": "idme"},
        headers=super_admin_headers,
    )
    assert response.status_code == 200, response.text
    key = await issue_api_key(client, super_admin_headers, portal["id"])
    return {"key": {"X-API-Key": key["api_key"]}, "client_id": portal["id"]}


def tool_url(code: str) -> str:
    return f"{LIST_URL}/{code}"


async def save(client: AsyncClient, setup: dict, code: str, **body):
    return await client.put(tool_url(code), json=body, headers=setup["key"])


async def test_screen_requires_api_key(client: AsyncClient) -> None:
    response = await client.get(LIST_URL)

    assert response.status_code == 401


async def test_list_groups_enabled_types_with_tool_cards(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(LIST_URL, headers=setup["key"])

    groups = response.json()["groups"]
    # Only "confirm" is enabled for this client; "gather" is hidden.
    assert [g["integration_type"]["code"] for g in groups] == ["confirm"]
    assert groups[0]["integration_type"]["description"] == (
        "Is this really the person?"
    )
    cards = {card["code"]: card for card in groups[0]["tools"]}
    assert list(cards) == ["entra", "idme", "login-gov"]
    assert cards["entra"]["status"] == "available"
    assert cards["idme"]["is_default"] is True
    assert cards["login-gov"]["status"] == "unavailable"


async def test_detail_describes_auth_and_missing_credentials(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(tool_url("entra"), headers=setup["key"])

    body = response.json()
    assert body["auth_method"] == "oauth2_client_credentials"
    assert body["connector_kind"] == "http"
    assert body["required_credentials"] == [
        "client_id",
        "client_secret",
        "tenant_id",
    ]
    assert body["missing_credentials"] == body["required_credentials"]
    assert body["can_test"] is True
    assert body["operations"] == ["organisation"]


@pytest.mark.parametrize("code", ["workday", "retired", "does-not-exist"])
async def test_tools_outside_scope_look_missing(
    client: AsyncClient, setup: dict, code: str
) -> None:
    response = await client.get(tool_url(code), headers=setup["key"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"


async def test_credentials_are_merged_and_never_returned(
    client: AsyncClient,
    setup: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await save(
        client,
        setup,
        "entra",
        credentials={"tenant_id": "t-1", "client_id": "app-1"},
    )
    second = await save(
        client, setup, "entra", credentials={"client_secret": SECRET}
    )

    assert first.json()["missing_credentials"] == ["client_secret"]
    assert second.json()["stored_credentials"] == [
        "client_id",
        "client_secret",
        "tenant_id",
    ]
    assert second.json()["missing_credentials"] == []
    assert second.json()["status"] == "not_tested"
    assert SECRET not in second.text
    async with session_factory() as session:
        secrets = (await session.scalars(select(LocalSecret))).all()
    assert all(SECRET not in secret.ciphertext for secret in secrets)


async def test_test_connection_marks_tool_connected(
    client: AsyncClient, setup: dict, providers: FakeProviders
) -> None:
    await save(
        client,
        setup,
        "entra",
        credentials={
            "tenant_id": "t-1",
            "client_id": "app-1",
            "client_secret": SECRET,
        },
    )

    response = await client.post(
        f"{tool_url('entra')}/test", headers=setup["key"]
    )

    body = response.json()
    assert response.status_code == 200
    assert (body["success"], body["called_provider"]) == (True, True)
    assert body["status"] == "connected"
    token_request = next(
        r for r in providers.requests if r.url.host == "login.example.com"
    )
    assert token_request.url.path == "/t-1/oauth2/token"
    assert parse_qs(token_request.content.decode())["client_secret"] == [
        SECRET
    ]
    api_call = providers.requests[-1]
    assert api_call.url.path == "/v1.0/organization"
    assert api_call.headers["Authorization"] == "Bearer tok"
    card = (await client.get(LIST_URL, headers=setup["key"])).json()
    entra = card["groups"][0]["tools"][0]
    assert (entra["status"], entra["is_enabled"]) == ("connected", True)
    assert entra["last_tested_at"] is not None


async def test_failed_test_is_reported_not_raised(
    client: AsyncClient, setup: dict, providers: FakeProviders
) -> None:
    await save(
        client,
        setup,
        "entra",
        credentials={
            "tenant_id": "t",
            "client_id": "a",
            "client_secret": "wrong",
        },
    )
    providers.reject_token = True

    response = await client.post(
        f"{tool_url('entra')}/test", headers=setup["key"]
    )

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is False
    assert body["status"] == "failed"
    assert "rejected the client credentials" in body["message"]
    detail = await client.get(tool_url("entra"), headers=setup["key"])
    assert detail.json()["last_test_message"] == body["message"]


async def test_test_without_credentials_names_what_is_missing(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.post(
        f"{tool_url('entra')}/test", headers=setup["key"]
    )

    body = response.json()
    assert body["success"] is False
    assert "credentials.client_secret" in body["message"]


async def test_tool_without_connector_cannot_be_tested(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.post(
        f"{tool_url('login-gov')}/test", headers=setup["key"]
    )

    assert response.json()["status"] == "unavailable"
    assert response.json()["success"] is False


async def test_removing_all_credentials_deletes_them(
    client: AsyncClient,
    setup: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await save(client, setup, "idme", credentials={"api_key": "k"})

    response = await save(
        client, setup, "idme", remove_credentials=["api_key"]
    )

    assert response.json()["stored_credentials"] == []
    async with session_factory() as session:
        assert await session.scalar(select(ClientToolCredential)) is None


async def test_disabling_a_tool_blocks_user_operations(
    client: AsyncClient, setup: dict
) -> None:
    await save(client, setup, "idme", credentials={"api_key": "k"})
    user_base = (
        "/api/v1/client/users/55555555-5555-4555-8555-555555555555/"
        "connections/confirm"
    )
    await client.put(user_base, json={"kwargs": {}}, headers=setup["key"])
    allowed = await client.post(
        f"{user_base}/operations/verify",
        json={"kwargs": {"id": "1"}},
        headers=setup["key"],
    )

    toggled = await save(client, setup, "idme", is_enabled=False)
    blocked = await client.post(
        f"{user_base}/operations/verify",
        json={"kwargs": {"id": "1"}},
        headers=setup["key"],
    )

    assert allowed.status_code == 200, allowed.text
    assert toggled.json()["status"] == "disabled"
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "integration_tool_disabled"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"credentials": {"Bad Name": "x"}},
        {"credentials": {"a": ""}},
        {"credentials": {"a": "x"}, "remove_credentials": ["a"]},
        {"is_enabled": True, "unknown": 1},
    ],
)
async def test_invalid_updates_are_rejected(
    client: AsyncClient, setup: dict, body: dict
) -> None:
    response = await client.put(
        tool_url("entra"), json=body, headers=setup["key"]
    )

    assert response.status_code == 422


async def test_testing_an_untouched_tool_does_not_turn_it_on(
    client: AsyncClient, setup: dict
) -> None:
    before = await client.get(tool_url("entra"), headers=setup["key"])

    await client.post(f"{tool_url('entra')}/test", headers=setup["key"])
    after = await client.get(tool_url("entra"), headers=setup["key"])

    assert before.json()["is_enabled"] is False
    assert after.json()["is_enabled"] is False


async def test_tool_without_connector_is_never_shown_enabled(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(tool_url("login-gov"), headers=setup["key"])

    assert response.json()["is_enabled"] is False
