import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.base import ConnectionOutcome, IntegrationConnector
from app.connectors.dependencies import get_connector_registry
from app.connectors.registry import ConnectorRegistry
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_types.models import IntegrationType

TOOLS_URL = "/v1/integration-tools"
CLIENT_TOOLS_URL = "/v1/client/integration-tools"


class AcmeConnector(IntegrationConnector):
    async def connect(
        self, api_token: str, *, region: str = "in", **extra: object
    ) -> ConnectionOutcome:
        return ConnectionOutcome()


@pytest.fixture(autouse=True)
def connector_registry(app: FastAPI) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register("acme-verify", AcmeConnector)
    app.dependency_overrides[get_connector_registry] = lambda: registry
    return registry


@pytest.fixture
async def tool_catalogue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Types confirm/gather/records and four tools linked to them.

    acme-verify   -> confirm, gather   (has a connector)
    globex-gather -> gather            (no connector)
    initech-legacy-> confirm           (inactive)
    umbrella-rec  -> records
    """
    async with session_factory() as session:
        confirm = IntegrationType(
            code="confirm", name="Confirm", display_order=1
        )
        gather = IntegrationType(code="gather", name="Gather", display_order=2)
        records = IntegrationType(
            code="records", name="Records", display_order=3
        )
        session.add_all(
            [
                IntegrationTool(
                    code="acme-verify",
                    name="Acme Verify",
                    provider="Acme",
                    display_order=1,
                    integration_types=[confirm, gather],
                ),
                IntegrationTool(
                    code="globex-gather",
                    name="Globex Gather",
                    provider="Globex",
                    display_order=2,
                    integration_types=[gather],
                ),
                IntegrationTool(
                    code="initech-legacy",
                    name="Initech Legacy",
                    display_order=3,
                    is_active=False,
                    integration_types=[confirm],
                ),
                IntegrationTool(
                    code="umbrella-rec",
                    name="Umbrella Records",
                    display_order=4,
                    integration_types=[records],
                ),
            ]
        )
        await session.commit()
