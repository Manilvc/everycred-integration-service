"""Business logic for browsing integration types."""

from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.integration_types.schemas import (
    DirectionFilter,
    IntegrationTypeFilters,
    IntegrationTypeResponse,
)
from app.shared.schemas import Page


class IntegrationTypeService:
    """Lists the integration types stored in the database.

    Attributes:
        repository: Persistence for integration type rows.
    """

    def __init__(self, repository: IntegrationTypeRepository) -> None:
        self.repository = repository

    async def list_integration_types(
        self, filters: IntegrationTypeFilters
    ) -> Page[IntegrationTypeResponse]:
        """Return one page of integration types matching ``filters``."""
        integration_types, total = await self.repository.list_page(
            include_inactive=filters.include_inactive,
            limit=filters.limit,
            offset=filters.offset,
            direction=(
                None
                if filters.direction is DirectionFilter.ALL
                else filters.direction.value
            ),
        )
        return Page[IntegrationTypeResponse](
            items=[
                IntegrationTypeResponse.model_validate(integration_type)
                for integration_type in integration_types
            ],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )
