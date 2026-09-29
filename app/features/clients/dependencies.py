"""Dependency providers and API key authentication for clients."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.core.config import SettingsDep
from app.core.database import DbSession
from app.core.secret_store import SecretStoreDep
from app.features.clients.exceptions import InvalidApiKeyError
from app.features.clients.models import Client
from app.features.clients.repository import (
    ClientApiKeyRepository,
    ClientIntegrationConfigRepository,
    ClientRepository,
    ClientToolCredentialRepository,
)
from app.features.clients.service import (
    ApiKeyService,
    ClientConfigurationService,
    ClientCredentialService,
    ClientService,
)
from app.features.integration_tools.repository import (
    IntegrationToolRepository,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)

API_KEY_HEADER = "X-API-Key"

# auto_error=False so a missing header returns our error envelope rather
# than FastAPI's default 403 body.
api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description="Client API key issued by a super admin.",
)


def get_client_service(session: DbSession) -> ClientService:
    """Build a client service bound to the request's session."""
    return ClientService(session, ClientRepository(session))


def get_api_key_service(
    session: DbSession, settings: SettingsDep
) -> ApiKeyService:
    """Build an API key service bound to the request's session."""
    return ApiKeyService(
        session=session,
        clients=ClientRepository(session),
        api_keys=ClientApiKeyRepository(session),
        settings=settings,
    )


def get_client_configuration_service(
    session: DbSession,
) -> ClientConfigurationService:
    """Build a configuration service bound to the request's session."""
    return ClientConfigurationService(
        session=session,
        clients=ClientRepository(session),
        configs=ClientIntegrationConfigRepository(session),
        integration_types=IntegrationTypeRepository(session),
        integration_tools=IntegrationToolRepository(session),
    )


def get_client_credential_service(
    session: DbSession, secret_store: SecretStoreDep
) -> ClientCredentialService:
    """Build a credential service bound to the request's session."""
    return ClientCredentialService(
        session=session,
        clients=ClientRepository(session),
        tools=IntegrationToolRepository(session),
        credentials=ClientToolCredentialRepository(session),
        secret_store=secret_store,
    )


ClientServiceDep = Annotated[ClientService, Depends(get_client_service)]
ClientCredentialServiceDep = Annotated[
    ClientCredentialService, Depends(get_client_credential_service)
]
ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]
ClientConfigurationServiceDep = Annotated[
    ClientConfigurationService, Depends(get_client_configuration_service)
]


async def get_current_client(
    presented_key: Annotated[str | None, Depends(api_key_scheme)],
    api_key_service: ApiKeyServiceDep,
) -> Client:
    """Return the client identified by the request's ``X-API-Key``.

    Raises:
        InvalidApiKeyError: The header is missing or the key is unusable.
    """
    if not presented_key:
        raise InvalidApiKeyError
    return await api_key_service.authenticate(presented_key)


CurrentClient = Annotated[Client, Depends(get_current_client)]
