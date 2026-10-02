from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.integration_types.models import IntegrationType

CLIENTS_URL = "/v1/clients"
OWN_CONFIGURATION_URL = "/v1/client/configuration"


@pytest.fixture
async def integration_types(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                IntegrationType(
                    code="confirm", name="Confirm", display_order=10
                ),
                IntegrationType(
                    code="gather", name="Gather", display_order=20
                ),
                IntegrationType(
                    code="retired",
                    name="Retired",
                    display_order=30,
                    is_active=False,
                ),
            ]
        )
        await session.commit()


async def create_client(
    client: AsyncClient,
    headers: dict[str, str],
    code: str = "issuer-portal",
) -> dict[str, Any]:
    response = await client.post(
        CLIENTS_URL,
        json={"code": code, "name": code.replace("-", " ").title()},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def issue_api_key(
    client: AsyncClient,
    headers: dict[str, str],
    client_id: str,
    **options: Any,
) -> dict[str, Any]:
    response = await client.post(
        f"{CLIENTS_URL}/{client_id}/api-keys",
        json={"name": "production", **options},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
