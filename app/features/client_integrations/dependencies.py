"""Dependency providers for the client Integrations screen."""

from typing import Annotated

from fastapi import Depends

from app.connectors.dependencies import ConnectorRegistryDep
from app.core.config import SettingsDep
from app.core.database import DbSession
from app.core.http_client import HttpClient
from app.core.secret_store import SecretStoreDep
from app.features.client_integrations.repository import (
    ClientToolConnectionRepository,
)
from app.features.client_integrations.service import ClientIntegrationService
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.integration_tools.repository import (
    IntegrationToolRepository,
)


def get_client_integration_service(
    session: DbSession,
    secret_store: SecretStoreDep,
    registry: ConnectorRegistryDep,
    http_client: HttpClient,
    settings: SettingsDep,
) -> ClientIntegrationService:
    """Build the service bound to the request's session."""
    return ClientIntegrationService(
        session=session,
        configs=ClientIntegrationConfigRepository(session),
        tools=IntegrationToolRepository(session),
        credentials=ClientToolCredentialRepository(session),
        connections=ClientToolConnectionRepository(session),
        secret_store=secret_store,
        registry=registry,
        http_client=http_client,
        settings=settings,
    )


ClientIntegrationServiceDep = Annotated[
    ClientIntegrationService, Depends(get_client_integration_service)
]
