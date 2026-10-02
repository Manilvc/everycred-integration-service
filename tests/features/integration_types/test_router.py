import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token
from app.features.integration_types.models import IntegrationType

LIST_URL = "/v1/integration-types"


@pytest.fixture
async def seeded_types(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                IntegrationType(
                    code="records", name="Records", display_order=40
                ),
                IntegrationType(
                    code="confirm", name="Confirm", display_order=10
                ),
                IntegrationType(
                    code="enforcement", name="Enforcement", display_order=30
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
