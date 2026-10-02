"""Dependency providers for the integration types feature."""

from typing import Annotated

from fastapi import Depends

from app.core.database import DbSession
from app.features.clients.repository import ClientIntegrationConfigRepository
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.integration_types.service import IntegrationTypeService


def get_integration_type_service(session: DbSession) -> IntegrationTypeService:
    """Build an integration type service bound to the request's session."""
    return IntegrationTypeService(
        repository=IntegrationTypeRepository(session),
        client_configs=ClientIntegrationConfigRepository(session),
    )


IntegrationTypeServiceDep = Annotated[
    IntegrationTypeService, Depends(get_integration_type_service)
]
