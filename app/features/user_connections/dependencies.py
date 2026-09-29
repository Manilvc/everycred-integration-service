"""Dependency providers for the user connections feature."""

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
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.user_connections.repository import UserConnectionRepository
from app.features.user_connections.service import UserConnectionService


def get_user_connection_service(
    session: DbSession,
    registry: ConnectorRegistryDep,
    secret_store: SecretStoreDep,
    http_client: HttpClient,
    settings: SettingsDep,
) -> UserConnectionService:
    """Build a connection service bound to the request's session."""
    return UserConnectionService(
        session=session,
        connections=UserConnectionRepository(session),
        client_configs=ClientIntegrationConfigRepository(session),
        integration_types=IntegrationTypeRepository(session),
        credentials=ClientToolCredentialRepository(session),
        tool_connections=ClientToolConnectionRepository(session),
        registry=registry,
        secret_store=secret_store,
        http_client=http_client,
        settings=settings,
    )


UserConnectionServiceDep = Annotated[
    UserConnectionService, Depends(get_user_connection_service)
]
