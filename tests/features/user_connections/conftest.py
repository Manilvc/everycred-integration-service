import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.base import (
    ConnectionOutcome,
    ConnectorContext,
    ConnectorError,
    IntegrationConnector,
)
from app.connectors.dependencies import get_connector_registry
from app.connectors.registry import ConnectorRegistry
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_types.models import IntegrationType
from tests.features.clients.conftest import create_client, issue_api_key

USER_UUID = "11111111-1111-4111-8111-111111111111"


@dataclass
class RecordedCall:
    context: ConnectorContext
    api_token: str
    region: str


recorded_calls: list[RecordedCall] = []


class RecordingConnector(IntegrationConnector):
    async def connect(
        self, api_token: str, *, region: str = "in"
    ) -> ConnectionOutcome:
        recorded_calls.append(RecordedCall(self.context, api_token, region))
        return ConnectionOutcome(details={"account_ref": f"acct-{region}"})


class RejectingConnector(IntegrationConnector):
    async def connect(self, api_token: str) -> ConnectionOutcome:
        raise ConnectorError("The provider rejected the credentials.")


class CrashingConnector(IntegrationConnector):
    async def connect(self, api_token: str) -> ConnectionOutcome:
        raise RuntimeError(f"unexpected failure using {api_token}")


class HangingConnector(IntegrationConnector):
    async def connect(self, api_token: str) -> ConnectionOutcome:
        await asyncio.sleep(10)
        return ConnectionOutcome()


@pytest.fixture(autouse=True)
def connector_registry(app: FastAPI) -> ConnectorRegistry:
    recorded_calls.clear()
    registry = ConnectorRegistry()
    registry.register("recording-tool", RecordingConnector)
    registry.register("rejecting-tool", RejectingConnector)
    registry.register("crashing-tool", CrashingConnector)
    registry.register("hanging-tool", HangingConnector)
    app.dependency_overrides[get_connector_registry] = lambda: registry
    return registry


# Integration type code -> tool the test client uses for it. "unwired"
# has a tool with no connector; "no-tool" is enabled without a tool.
TOOL_BY_TYPE = {
    "confirm": "recording-tool",
    "gather": "rejecting-tool",
    "enforcement": "crashing-tool",
    "records": "hanging-tool",
    "unwired": "unwired-tool",
    "no-tool": None,
}


@pytest.fixture
async def integration_catalogue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    type_codes = [*TOOL_BY_TYPE, "not-enabled"]
    async with session_factory() as session:
        types_by_code = {
            code: IntegrationType(
                code=code, name=code.title(), display_order=position
            )
            for position, code in enumerate(type_codes)
        }
        session.add_all(types_by_code.values())
        session.add_all(
            [
                IntegrationTool(
                    code=tool_code,
                    name=tool_code.title(),
                    integration_types=[types_by_code[type_code]],
                )
                for type_code, tool_code in TOOL_BY_TYPE.items()
                if tool_code is not None
            ]
        )
        await session.commit()


async def enable_integrations(
    client: AsyncClient,
    admin_headers: dict[str, str],
    client_id: str,
    codes: list[str],
    settings: dict[str, Any] | None = None,
) -> None:
    for code in codes:
        response = await client.put(
            f"/v1/clients/{client_id}/integrations/{code}",
            json={
                "tool_code": TOOL_BY_TYPE.get(code),
                "settings": settings or {},
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text


@pytest.fixture
async def client_headers(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    integration_catalogue: None,
) -> dict[str, str]:
    """API key headers for a client with every test integration enabled."""
    portal = await create_client(client, super_admin_headers)
    await enable_integrations(
        client,
        super_admin_headers,
        portal["id"],
        list(TOOL_BY_TYPE),
        settings={"environment": "sandbox"},
    )
    issued = await issue_api_key(client, super_admin_headers, portal["id"])
    return {"X-API-Key": issued["api_key"]}


def connection_url(code: str | None = None, user_uuid: str = USER_UUID) -> str:
    base = f"/v1/client/users/{user_uuid}/connections"
    return f"{base}/{code}" if code else base
