"""Dependency providers for catalogue id resolution."""

from typing import Annotated

from fastapi import Depends

from app.core.database import DbSession
from app.features.catalog.service import CatalogService
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
    IntegrationToolRepository,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)


def get_catalog_service(session: DbSession) -> CatalogService:
    """Build the service bound to the request's session."""
    return CatalogService(
        integration_types=IntegrationTypeRepository(session),
        tools=IntegrationToolRepository(session),
        fields=IntegrationToolFieldRepository(session),
    )


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
