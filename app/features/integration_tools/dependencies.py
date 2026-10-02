"""Dependency providers for the integration tools feature."""

from typing import Annotated

from fastapi import Depends

from app.connectors.dependencies import ConnectorRegistryDep
from app.core.database import DbSession
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientRepository,
)
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
    IntegrationToolRepository,
)
from app.features.integration_tools.service import IntegrationToolService
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)


def get_integration_tool_service(
    session: DbSession, registry: ConnectorRegistryDep
) -> IntegrationToolService:
    """Build a tool service bound to the request's session."""
    return IntegrationToolService(
        session=session,
        tools=IntegrationToolRepository(session),
        tool_fields=IntegrationToolFieldRepository(session),
        integration_types=IntegrationTypeRepository(session),
        client_configs=ClientIntegrationConfigRepository(session),
        clients=ClientRepository(session),
        registry=registry,
    )


IntegrationToolServiceDep = Annotated[
    IntegrationToolService, Depends(get_integration_tool_service)
]
