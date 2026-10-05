import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.catalog.schemas import MAX_IDS_PER_KIND
from app.features.integration_tools.models import (
    IntegrationTool,
    IntegrationToolField,
)
from app.features.integration_types.models import IntegrationType
from tests.features.clients.conftest import create_client, issue_api_key

RESOLVE_URL = "/v1/client/catalog/resolve"


@pytest.fixture
async def portal(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> dict:
    created = await create_client(client, super_admin_headers)
    key = await issue_api_key(client, super_admin_headers, created["id"])
    return {**created, "headers": {"X-API-Key": key["api_key"]}}


@pytest.fixture
async def catalogue(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    portal: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, uuid.UUID]:
    """Seed one type of each state, a tool, and fields in three scopes."""
    other = await create_client(client, super_admin_headers, code="other")
    async with session_factory() as session:
        declare = IntegrationType(code="declare", name="Declare")
        retired = IntegrationType(
            code="retired", name="Retired", is_active=False
        )
        wallet = IntegrationTool(
            code="holder-wallet-app",
            name="Holder Wallet App",
            integration_types=[declare],
        )
        session.add_all([declare, retired, wallet])
        await session.flush()
        global_field = IntegrationToolField(
            integration_tool_id=wallet.id,
            flow="kyc",
            key="full_name",
            label="Full Name",
        )
        own_field = IntegrationToolField(
            integration_tool_id=wallet.id,
            client_id=uuid.UUID(portal["id"]),
            flow="kyc",
            key="employee.id",
            label="Employee ID",
        )
        foreign_field = IntegrationToolField(
            integration_tool_id=wallet.id,
            client_id=uuid.UUID(other["id"]),
            flow="kyc",
            key="secret.key",
        )
        session.add_all([global_field, own_field, foreign_field])
        await session.commit()
        return {
            "declare": declare.id,
            "retired": retired.id,
            "wallet": wallet.id,
            "global_field": global_field.id,
            "own_field": own_field.id,
            "foreign_field": foreign_field.id,
        }


async def resolve(
    client: AsyncClient, headers: dict[str, str], **ids: list[uuid.UUID]
):
    body = {kind: [str(item) for item in items] for kind, items in ids.items()}
    return await client.post(RESOLVE_URL, json=body, headers=headers)


async def test_resolve_returns_names_and_field_keys(
    client: AsyncClient, portal: dict, catalogue: dict[str, uuid.UUID]
) -> None:
    response = await resolve(
        client,
        portal["headers"],
        type_ids=[catalogue["declare"]],
        tool_ids=[catalogue["wallet"]],
        field_ids=[catalogue["global_field"]],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["types"] == [
        {
            "id": str(catalogue["declare"]),
            "code": "declare",
            "name": "Declare",
            "description": None,
            "is_active": True,
        }
    ]
    assert body["tools"] == [
        {
            "id": str(catalogue["wallet"]),
            "code": "holder-wallet-app",
            "name": "Holder Wallet App",
            "is_active": True,
        }
    ]
    assert body["fields"] == [
        {
            "id": str(catalogue["global_field"]),
            "integration_tool_id": str(catalogue["wallet"]),
            "flow": "kyc",
            "key": "full_name",
            "label": "Full Name",
        }
    ]
    assert body["not_found"] == {
        "type_ids": [],
        "tool_ids": [],
        "field_ids": [],
    }


async def test_resolve_includes_inactive_types(
    client: AsyncClient, portal: dict, catalogue: dict[str, uuid.UUID]
) -> None:
    response = await resolve(
        client, portal["headers"], type_ids=[catalogue["retired"]]
    )

    assert response.json()["types"][0]["is_active"] is False


async def test_resolve_reports_unknown_ids_instead_of_failing(
    client: AsyncClient, portal: dict, catalogue: dict[str, uuid.UUID]
) -> None:
    unknown = uuid.uuid4()

    response = await resolve(
        client,
        portal["headers"],
        type_ids=[catalogue["declare"], unknown],
        field_ids=[unknown],
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["code"] for item in body["types"]] == ["declare"]
    assert body["not_found"]["type_ids"] == [str(unknown)]
    assert body["not_found"]["field_ids"] == [str(unknown)]


async def test_resolve_looks_up_duplicate_ids_once(
    client: AsyncClient, portal: dict, catalogue: dict[str, uuid.UUID]
) -> None:
    response = await resolve(
        client,
        portal["headers"],
        tool_ids=[catalogue["wallet"], catalogue["wallet"]],
    )

    assert len(response.json()["tools"]) == 1


async def test_resolve_hides_another_clients_fields(
    client: AsyncClient, portal: dict, catalogue: dict[str, uuid.UUID]
) -> None:
    response = await resolve(
        client,
        portal["headers"],
        field_ids=[catalogue["own_field"], catalogue["foreign_field"]],
    )

    body = response.json()
    assert [item["key"] for item in body["fields"]] == ["employee.id"]
    assert body["not_found"]["field_ids"] == [str(catalogue["foreign_field"])]


async def test_resolve_requires_an_api_key(client: AsyncClient) -> None:
    response = await client.post(RESOLVE_URL, json={})

    assert response.status_code == 401


async def test_resolve_rejects_too_many_ids(
    client: AsyncClient, portal: dict
) -> None:
    too_many = [uuid.uuid4() for _ in range(MAX_IDS_PER_KIND + 1)]

    response = await resolve(client, portal["headers"], type_ids=too_many)

    assert response.status_code == 422
