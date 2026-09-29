import json
import logging
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.http_client import get_http_client
from app.core.secret_store import LocalSecret
from app.features.clients.models import ClientToolCredential
from app.features.integration_types.models import IntegrationType
from app.features.user_connections.models import IntegrationOperationLog
from tests.features.clients.conftest import create_client, issue_api_key

USER = "33333333-3333-4333-8333-333333333333"
API_TOKEN = "sp-token-do-not-leak"
TOOL_URL = "/api/v1/integration-tools/acme-http"
BASE = f"/api/v1/client/users/{USER}/connections/confirm"

TOOL_DEFINITION = {
    "name": "Acme HTTP",
    "provider": "Acme",
    "integration_types": ["confirm"],
    "connector_config": {
        "type": "http",
        "environments": {"sandbox": "http://127.0.0.1:9/api/v1"},
        "default_environment": "sandbox",
        "headers": {"Authorization": "Bearer {credentials.api_token}"},
        "operations": {
            "ping": {"method": "GET", "path": "/ping"},
            "verify_document": {
                "path": "/verify",
                "description": "Verify an identity document",
                "body": {
                    "id_number": "{kwargs.id_number}",
                    "consent": "{kwargs.consent}",
                },
            },
        },
        "connect_operation": "ping",
    },
}


class FakeProvider:
    """Stands in for the provider; records requests, returns a reply."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: Callable[[httpx.Request], httpx.Response] = lambda _: (
            httpx.Response(
                200, json={"success": True, "data": {"status": "valid"}}
            )
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.reply(request)


@pytest.fixture
def provider(app: FastAPI) -> FakeProvider:
    fake = FakeProvider()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_http_client] = lambda: mock_client
    return fake


@pytest.fixture
async def setup(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    provider: FakeProvider,
) -> dict:
    async with session_factory() as session:
        session.add(IntegrationType(code="confirm", name="Confirm"))
        await session.commit()
    tool = await client.put(
        TOOL_URL, json=TOOL_DEFINITION, headers=super_admin_headers
    )
    assert tool.status_code == 200, tool.text
    portal = await create_client(client, super_admin_headers)
    config = await client.put(
        f"/api/v1/clients/{portal['id']}/integrations/confirm",
        json={"tool_code": "acme-http"},
        headers=super_admin_headers,
    )
    assert config.status_code == 200, config.text
    credentials = await client.put(
        f"/api/v1/clients/{portal['id']}/tools/acme-http/credentials",
        json={"credentials": {"api_token": API_TOKEN}},
        headers=super_admin_headers,
    )
    assert credentials.status_code == 200, credentials.text
    key = await issue_api_key(client, super_admin_headers, portal["id"])
    return {
        "client_id": portal["id"],
        "key": {"X-API-Key": key["api_key"]},
        "admin": super_admin_headers,
    }


async def save_inputs(
    client: AsyncClient, setup: dict, kwargs: dict | None = None
) -> None:
    response = await client.put(
        BASE, json={"kwargs": kwargs or {"consent": "Y"}}, headers=setup["key"]
    )
    assert response.status_code == 200, response.text


async def run(client: AsyncClient, setup: dict, operation: str, **kwargs):
    return await client.post(
        f"{BASE}/operations/{operation}",
        json={"kwargs": kwargs},
        headers=setup["key"],
    )


async def test_tool_definition_describes_http_operations(
    client: AsyncClient, setup: dict
) -> None:
    response = await client.get(
        "/api/v1/client/integration-tools", headers=setup["key"]
    )

    connector = response.json()["items"][0]["connector"]
    assert connector["kind"] == "http"
    assert connector["is_available"] is True
    verify = next(
        op for op in connector["operations"] if op["name"] == "verify_document"
    )
    assert verify["required_kwargs"] == ["consent", "id_number"]
    assert verify["required_credentials"] == ["api_token"]


async def test_operation_merges_saved_and_request_inputs(
    client: AsyncClient, setup: dict, provider: FakeProvider
) -> None:
    await save_inputs(client, setup)

    response = await run(client, setup, "verify_document", id_number="ABC1")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "operation": "verify_document",
        "integration_type_code": "confirm",
        "tool_code": "acme-http",
        "success": True,
        "provider_status_code": 200,
        "data": {"status": "valid"},
        "message": None,
    }
    sent = provider.requests[-1]
    assert sent.headers["Authorization"] == f"Bearer {API_TOKEN}"
    assert json.loads(sent.content) == {"id_number": "ABC1", "consent": "Y"}


async def test_operation_is_audited_without_inputs(
    client: AsyncClient,
    setup: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await save_inputs(client, setup)
    await run(client, setup, "verify_document", id_number="ABC1")

    async with session_factory() as session:
        log = await session.scalar(select(IntegrationOperationLog))

    assert log is not None
    assert (log.operation, log.tool_code, log.succeeded) == (
        "verify_document",
        "acme-http",
        True,
    )
    assert log.provider_status_code == 200
    assert "ABC1" not in repr(vars(log))


async def test_provider_rejection_is_success_false(
    client: AsyncClient, setup: dict, provider: FakeProvider
) -> None:
    await save_inputs(client, setup)
    provider.reply = lambda _: httpx.Response(
        200, json={"success": False, "data": None, "message": "Invalid ID"}
    )

    response = await run(client, setup, "verify_document", id_number="BAD")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["message"] == "Invalid ID"


async def test_missing_inputs_are_named(
    client: AsyncClient, setup: dict
) -> None:
    await save_inputs(client, setup, kwargs={"other": 1})

    response = await run(client, setup, "verify_document")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "missing_inputs"
    assert "kwargs.consent" in error["message"]
    assert "kwargs.id_number" in error["message"]


async def test_missing_credentials_are_reported_as_conflict(
    client: AsyncClient, setup: dict
) -> None:
    await save_inputs(client, setup)
    await client.delete(
        f"/api/v1/clients/{setup['client_id']}/tools/acme-http/credentials",
        headers=setup["admin"],
    )

    response = await run(client, setup, "verify_document", id_number="A")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tool_credentials_missing"


async def test_unknown_operation_returns_404(
    client: AsyncClient, setup: dict
) -> None:
    await save_inputs(client, setup)

    response = await run(client, setup, "drop_tables")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "operation_not_found"


async def test_provider_garbage_returns_502(
    client: AsyncClient, setup: dict, provider: FakeProvider
) -> None:
    await save_inputs(client, setup)
    provider.reply = lambda _: httpx.Response(500, text="Internal error")

    response = await run(client, setup, "verify_document", id_number="A")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "operation_failed"


async def test_operation_requires_saved_connection(
    client: AsyncClient, setup: dict
) -> None:
    response = await run(client, setup, "verify_document", id_number="A")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "connection_not_found"


async def test_connect_runs_connect_operation(
    client: AsyncClient, setup: dict, provider: FakeProvider
) -> None:
    await save_inputs(client, setup)

    response = await client.post(f"{BASE}/connect", headers=setup["key"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    assert provider.requests[-1].url.path.endswith("/ping")


async def test_credentials_never_leave_the_secret_store(
    client: AsyncClient,
    setup: dict,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    credentials_url = (
        f"/api/v1/clients/{setup['client_id']}/tools/acme-http/credentials"
    )

    shown = await client.get(credentials_url, headers=setup["admin"])
    await save_inputs(client, setup)
    result = await run(client, setup, "verify_document", id_number="A")

    assert shown.json()["credential_names"] == ["api_token"]
    for response in (shown, result):
        assert API_TOKEN not in response.text
    assert API_TOKEN not in caplog.text
    async with session_factory() as session:
        reference = await session.scalar(select(ClientToolCredential))
        secrets = (await session.scalars(select(LocalSecret))).all()
    assert reference.secret_reference.startswith("local:")
    assert all(API_TOKEN not in secret.ciphertext for secret in secrets)


async def test_tool_definition_rejects_literal_auth_header(
    client: AsyncClient, setup: dict
) -> None:
    definition = json.loads(json.dumps(TOOL_DEFINITION))
    definition["connector_config"]["headers"] = {
        "Authorization": "Bearer pasted-real-token"
    }

    response = await client.put(
        TOOL_URL, json=definition, headers=setup["admin"]
    )

    assert response.status_code == 422
    assert "pasted-real-token" not in response.text


async def test_tool_definition_rejects_unknown_types(
    client: AsyncClient, setup: dict
) -> None:
    definition = {**TOOL_DEFINITION, "integration_types": ["confirm", "nope"]}

    response = await client.put(
        TOOL_URL, json=definition, headers=setup["admin"]
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_integration_types"


async def test_tool_definition_requires_super_admin(
    client: AsyncClient,
) -> None:
    response = await client.put(TOOL_URL, json=TOOL_DEFINITION)

    assert response.status_code == 401
