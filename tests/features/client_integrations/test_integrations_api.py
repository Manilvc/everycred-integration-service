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
        declare = IntegrationType(
            code="declare",
            name="Declare",
            description="Holder & issuer input",
            display_order=3,
        )
        session.add_all(
            [
                IntegrationTool(
                    code="holder-wallet-app",
                    name="Holder Wallet App",
                    description="Wallet Application",
                    integration_types=[declare],
                ),
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


def systems_by_key(body: dict) -> dict[str, dict[str, dict]]:
    return {
        group["key"]: {system["code"]: system for system in group["systems"]}
        for group in body["data"]["groups"]
    }


async def test_list_matches_the_everycred_screen_shape(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(LIST_URL, headers=setup["key"])

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["message"] == "Integrations retrieved successfully."
    confirm = body["data"]["groups"][0]
    assert confirm["key"] == "confirm"
    assert confirm["label"] == "CONFIRM - Is this really the person?"
    assert confirm["description"] == "Is this really the person?"
    idme = next(s for s in confirm["systems"] if s["code"] == "idme")
    assert idme["source_role_id"] == confirm["id"]
    assert set(idme) == {
        "id",
        "code",
        "source_role_id",
        "name",
        "status_note",
        "is_connected",
        "is_active",
        "is_default",
        "status",
        "last_tested_at",
    }


async def test_list_shows_every_type_but_only_enabled_tools(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(LIST_URL, headers=setup["key"])

    systems = systems_by_key(response.json())
    # Every active type is a group; only "confirm" is enabled for this
    # client, so "gather" has no systems.
    assert list(systems) == ["confirm", "gather", "declare"]
    assert list(systems["confirm"]) == ["entra", "idme", "login-gov"]
    assert systems["gather"] == {}
    assert systems["confirm"]["entra"]["status"] == "available"
    assert systems["confirm"]["entra"]["is_connected"] is False
    assert systems["confirm"]["idme"]["is_default"] is True
    assert systems["confirm"]["login-gov"]["status"] == "unavailable"


async def test_built_in_wallet_is_listed_for_every_client(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(LIST_URL, headers=setup["key"])

    wallet = systems_by_key(response.json())["declare"]["holder-wallet-app"]
    assert wallet["name"] == "Holder Wallet App"
    assert wallet["status_note"] == "Wallet Application"
    assert wallet["status"] == "connected"
    assert (wallet["is_connected"], wallet["is_active"]) == (True, True)
    assert wallet["is_default"] is True


async def test_built_in_wallet_can_be_opened_tested_and_switched_off(
    client: AsyncClient, setup: dict
) -> None:
    detail = await client.get(
        tool_url("holder-wallet-app"), headers=setup["key"]
    )
    tested = await client.post(
        f"{tool_url('holder-wallet-app')}/test", headers=setup["key"]
    )
    switched_off = await save(
        client, setup, "holder-wallet-app", is_enabled=False
    )
    listed = await client.get(LIST_URL, headers=setup["key"])

    assert detail.status_code == 200
    assert detail.json()["missing_credentials"] == []
    assert [t["code"] for t in detail.json()["integration_types"]] == [
        "declare"
    ]
    assert tested.json()["success"] is True
    assert tested.json()["called_provider"] is False
    assert switched_off.json()["status"] == "disabled"
    wallet = systems_by_key(listed.json())["declare"]["holder-wallet-app"]
    assert (wallet["status"], wallet["is_connected"]) == ("disabled", False)
    assert wallet["is_active"] is False


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
    listed = (await client.get(LIST_URL, headers=setup["key"])).json()
    entra = systems_by_key(listed)["confirm"]["entra"]
    assert (entra["status"], entra["is_active"]) == ("connected", True)
    assert entra["is_connected"] is True
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


USER = "55555555-5555-4555-8555-555555555555"
OTHER_USER = "66666666-6666-4666-8666-666666666666"


def user_url(user: str = USER) -> str:
    return f"/api/v1/client/users/{user}/integrations"


async def user_systems(
    client: AsyncClient, setup: dict, user: str = USER
) -> dict[str, dict[str, dict]]:
    response = await client.get(user_url(user), headers=setup["key"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["message"] == "User integrations retrieved successfully."
    assert body["data"]["user_uuid"] == user
    return systems_by_key(body)


async def connect_user_to_idme(client: AsyncClient, setup: dict) -> None:
    saved = await save(client, setup, "idme", credentials={"api_key": "k"})
    assert saved.status_code == 200, saved.text
    params = await client.put(
        f"/api/v1/client/users/{USER}/connections/confirm",
        json={"kwargs": {"id": "doc-1"}},
        headers=setup["key"],
    )
    assert params.status_code == 200, params.text
    connected = await client.post(
        f"/api/v1/client/users/{USER}/connections/confirm/connect",
        headers=setup["key"],
    )
    assert connected.status_code == 200, connected.text


async def test_user_listing_requires_api_key(client: AsyncClient) -> None:
    response = await client.get(user_url())

    assert response.status_code == 401


async def test_unknown_user_sees_routed_tool_and_built_ins(
    client: AsyncClient, setup: dict
) -> None:
    systems = await user_systems(client, setup)

    # Only the tool "confirm" routes users through is listed, not every
    # tool serving the type; "gather" is not enabled for this client.
    assert list(systems) == ["confirm", "gather", "declare"]
    assert list(systems["confirm"]) == ["idme"]
    assert systems["gather"] == {}
    idme = systems["confirm"]["idme"]
    # The client has not stored ID.me's credentials yet.
    assert (idme["status"], idme["is_active"]) == ("unavailable", False)
    assert idme["tool_status"] == "available"
    assert idme["connection_id"] is None
    wallet = systems["declare"]["holder-wallet-app"]
    assert (wallet["status"], wallet["is_connected"]) == ("connected", True)


async def test_user_status_follows_the_connection(
    client: AsyncClient, setup: dict
) -> None:
    await save(client, setup, "idme", credentials={"api_key": "k"})
    before = (await user_systems(client, setup))["confirm"]["idme"]
    await client.put(
        f"/api/v1/client/users/{USER}/connections/confirm",
        json={"kwargs": {"id": "doc-1"}},
        headers=setup["key"],
    )
    saved = (await user_systems(client, setup))["confirm"]["idme"]
    await client.post(
        f"/api/v1/client/users/{USER}/connections/confirm/connect",
        headers=setup["key"],
    )
    connected = (await user_systems(client, setup))["confirm"]["idme"]

    assert (before["status"], before["is_active"]) == ("not_connected", True)
    assert saved["status"] == "pending"
    assert saved["connection_id"] is not None
    assert connected["status"] == "connected"
    assert connected["is_connected"] is True
    assert connected["last_connected_at"] is not None
    assert connected["last_error"] is None


async def test_switched_off_tool_is_unavailable_but_keeps_history(
    client: AsyncClient, setup: dict
) -> None:
    await connect_user_to_idme(client, setup)
    await save(client, setup, "idme", is_enabled=False)

    idme = (await user_systems(client, setup))["confirm"]["idme"]

    assert idme["status"] == "unavailable"
    assert (idme["is_connected"], idme["is_active"]) == (False, False)
    assert idme["tool_status"] == "disabled"
    assert idme["connection_id"] is not None
    assert idme["last_connected_at"] is not None


async def test_users_and_clients_are_kept_apart(
    client: AsyncClient,
    setup: dict,
    super_admin_headers: dict[str, str],
) -> None:
    await connect_user_to_idme(client, setup)
    other_portal = await create_client(
        client, super_admin_headers, code="other-portal"
    )
    await client.put(
        f"/api/v1/clients/{other_portal['id']}/integrations/confirm",
        json={"tool_code": "idme"},
        headers=super_admin_headers,
    )
    other_key = await issue_api_key(
        client, super_admin_headers, other_portal["id"]
    )

    other_user = (await user_systems(client, setup, OTHER_USER))["confirm"]
    same_user_other_client = await client.get(
        user_url(), headers={"X-API-Key": other_key["api_key"]}
    )

    assert other_user["idme"]["status"] == "not_connected"
    seen_by_other = systems_by_key(same_user_other_client.json())
    assert seen_by_other["confirm"]["idme"]["connection_id"] is None


async def test_status_filter_returns_only_connected_tools(
    client: AsyncClient, setup: dict
) -> None:
    await save(client, setup, "idme", credentials={"api_key": "k"})

    before = await client.get(
        user_url(), params={"status": "connected"}, headers=setup["key"]
    )
    await connect_user_to_idme(client, setup)
    after = await client.get(
        user_url(), params={"status": "connected"}, headers=setup["key"]
    )

    # No connection yet: ID.me is left out; only the built-in wallet is
    # connected. Every group is still listed.
    assert {
        key: list(systems)
        for key, systems in systems_by_key(before.json()).items()
    } == {"confirm": [], "gather": [], "declare": ["holder-wallet-app"]}
    assert list(systems_by_key(after.json())["confirm"]) == ["idme"]


async def test_status_filter_accepts_several_statuses(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(
        user_url(),
        params=[("status", "not_connected"), ("status", "unavailable")],
        headers=setup["key"],
    )

    systems = systems_by_key(response.json())
    assert list(systems["confirm"]) == ["idme"]
    assert systems["declare"] == {}


async def test_unknown_status_filter_is_rejected(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(
        user_url(), params={"status": "online"}, headers=setup["key"]
    )

    assert response.status_code == 422
