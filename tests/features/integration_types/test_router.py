import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token
from app.features.integration_types.models import IntegrationType
from tests.features.clients.conftest import create_client, issue_api_key

LIST_URL = "/v1/integration-types"


@pytest.fixture
async def seeded_types(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                IntegrationType(
                    code="records",
                    name="Records",
                    display_order=40,
                    direction="outbound",
                ),
                IntegrationType(
                    code="confirm", name="Confirm", display_order=10
                ),
                IntegrationType(
                    code="enforcement",
                    name="Enforcement",
                    display_order=30,
                    direction="outbound",
                ),
                IntegrationType(
                    code="gather", name="Gather", display_order=20
                ),
                IntegrationType(
                    code="legacy",
                    name="Legacy",
                    display_order=50,
                    is_active=False,
                ),
            ]
        )
        await session.commit()


async def test_listing_requires_a_token(client: AsyncClient) -> None:
    response = await client.get(LIST_URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


async def test_listing_rejects_tokens_for_other_roles(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    me = await client.get("/v1/super-admins/me", headers=super_admin_headers)
    other_role = create_access_token(
        me.json()["id"], "tenant_admin", get_settings()
    )

    response = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {other_role.token}"}
    )

    assert response.status_code == 403


async def test_lists_active_types_in_display_order(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
) -> None:
    response = await client.get(LIST_URL, headers=super_admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["code"] for item in body["items"]] == [
        "confirm",
        "gather",
        "enforcement",
        "records",
    ]
    assert body["total"] == 4
    assert body["limit"] == 20
    assert body["offset"] == 0


async def test_include_inactive_returns_every_type(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
) -> None:
    response = await client.get(
        LIST_URL,
        params={"include_inactive": "true"},
        headers=super_admin_headers,
    )

    body = response.json()
    assert body["total"] == 5
    assert body["items"][-1]["code"] == "legacy"
    assert body["items"][-1]["is_active"] is False


async def test_pagination_returns_requested_slice(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
) -> None:
    response = await client.get(
        LIST_URL,
        params={"limit": 2, "offset": 1},
        headers=super_admin_headers,
    )

    body = response.json()
    assert [item["code"] for item in body["items"]] == [
        "gather",
        "enforcement",
    ]
    assert body["total"] == 4


async def test_type_added_to_database_appears_without_code_change(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    seeded_types: None,
) -> None:
    async with session_factory() as session:
        session.add(
            IntegrationType(code="payments", name="Payments", display_order=5)
        )
        await session.commit()

    response = await client.get(LIST_URL, headers=super_admin_headers)

    assert response.json()["items"][0]["code"] == "payments"
    assert response.json()["total"] == 5


async def test_empty_catalogue_returns_empty_page(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(LIST_URL, headers=super_admin_headers)

    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
    }


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"unknown": "x"}],
)
async def test_invalid_query_parameters_are_rejected(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    params: dict[str, object],
) -> None:
    response = await client.get(
        LIST_URL, params=params, headers=super_admin_headers
    )

    assert response.status_code == 422


async def test_every_type_shows_its_direction(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
) -> None:
    response = await client.get(LIST_URL, headers=super_admin_headers)

    assert {
        item["code"]: item["direction"] for item in response.json()["items"]
    } == {
        "confirm": "inbound",
        "gather": "inbound",
        "enforcement": "outbound",
        "records": "outbound",
    }


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("inbound", ["confirm", "gather"]),
        ("outbound", ["enforcement", "records"]),
        ("all", ["confirm", "gather", "enforcement", "records"]),
    ],
)
async def test_direction_filters_the_listing(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
    direction: str,
    expected: list[str],
) -> None:
    response = await client.get(
        LIST_URL, params={"direction": direction}, headers=super_admin_headers
    )

    body = response.json()
    assert [item["code"] for item in body["items"]] == expected
    assert body["total"] == len(expected)


async def test_new_types_are_inbound_by_default(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(IntegrationType(code="declare", name="Declare"))
        await session.commit()

    response = await client.get(LIST_URL, headers=super_admin_headers)

    assert response.json()["items"][0]["direction"] == "inbound"


async def test_unknown_direction_is_rejected(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        LIST_URL, params={"direction": "sideways"}, headers=super_admin_headers
    )

    assert response.status_code == 422


CLIENT_LIST_URL = "/v1/client/integration-types"


@pytest.fixture
async def client_key(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    seeded_types: None,
) -> dict[str, str]:
    """API key headers for a client with only "gather" enabled."""
    portal = await create_client(client, super_admin_headers)
    enabled = await client.put(
        f"/v1/clients/{portal['id']}/integrations/gather",
        json={"tool_code": None},
        headers=super_admin_headers,
    )
    assert enabled.status_code == 200, enabled.text
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    return {"X-API-Key": issued["api_key"]}


async def test_client_lists_types_with_its_api_key(
    client: AsyncClient, client_key: dict[str, str]
) -> None:
    response = await client.get(CLIENT_LIST_URL, headers=client_key)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    # Every active type, in display order; inactive "legacy" is hidden.
    assert [item["code"] for item in items] == [
        "confirm",
        "gather",
        "enforcement",
        "records",
    ]
    assert {item["code"]: item["is_enabled"] for item in items} == {
        "confirm": False,
        "gather": True,
        "enforcement": False,
        "records": False,
    }
    assert items[0]["direction"] == "inbound"


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("inbound", ["confirm", "gather"]),
        ("outbound", ["enforcement", "records"]),
        ("all", ["confirm", "gather", "enforcement", "records"]),
    ],
)
async def test_client_listing_filters_by_direction(
    client: AsyncClient,
    client_key: dict[str, str],
    direction: str,
    expected: list[str],
) -> None:
    response = await client.get(
        CLIENT_LIST_URL, params={"direction": direction}, headers=client_key
    )

    assert [item["code"] for item in response.json()["items"]] == expected


async def test_client_listing_requires_an_api_key(
    client: AsyncClient, super_admin_headers: dict[str, str]
) -> None:
    anonymous = await client.get(CLIENT_LIST_URL)
    with_admin_token = await client.get(
        CLIENT_LIST_URL, headers=super_admin_headers
    )

    assert anonymous.status_code == 401
    assert with_admin_token.status_code == 401


async def test_client_listing_does_not_offer_inactive_types(
    client: AsyncClient, client_key: dict[str, str]
) -> None:
    response = await client.get(
        CLIENT_LIST_URL,
        params={"include_inactive": "true"},
        headers=client_key,
    )

    assert response.status_code == 422
