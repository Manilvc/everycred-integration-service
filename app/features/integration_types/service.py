"""Business logic for browsing integration types."""

from app.features.clients.models import Client
from app.features.clients.repository import ClientIntegrationConfigRepository
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.integration_types.schemas import (
    ClientIntegrationTypeFilters,
    ClientIntegrationTypeResponse,
    DirectionFilter,
    IntegrationTypeFilters,
    IntegrationTypeResponse,
)
from app.shared.schemas import Page


class IntegrationTypeService:
    """Lists the integration types stored in the database.

    Attributes:
        repository: Persistence for integration type rows.
        client_configs: Tells which types a client has enabled.
    """

    def __init__(
        self,
        repository: IntegrationTypeRepository,
        client_configs: ClientIntegrationConfigRepository,
    ) -> None:
        self.repository = repository
        self.client_configs = client_configs

    async def list_integration_types(
        self, filters: IntegrationTypeFilters
    ) -> Page[IntegrationTypeResponse]:
        """Return one page of integration types matching ``filters``."""
        integration_types, total = await self.repository.list_page(
            include_inactive=filters.include_inactive,
            limit=filters.limit,
            offset=filters.offset,
            direction=_direction_value(filters.direction),
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

    async def list_for_client(
        self, client: Client, filters: ClientIntegrationTypeFilters
    ) -> Page[ClientIntegrationTypeResponse]:
        """Return the active types, each saying if the client has it on.

        Every active type is listed, as on the client's Integrations
        screen; ``is_enabled`` tells which ones a super admin enabled.
        """
        integration_types, total = await self.repository.list_page(
            include_inactive=False,
            limit=filters.limit,
            offset=filters.offset,
            direction=_direction_value(filters.direction),
        )
        enabled_rows = await self.client_configs.list_with_types(
            client.id, usable_only=True
        )
        enabled_type_ids = {
            integration_type.id for _, integration_type in enabled_rows
        }
        return Page[ClientIntegrationTypeResponse](
            items=[
                ClientIntegrationTypeResponse(
                    **IntegrationTypeResponse.model_validate(
                        integration_type
                    ).model_dump(),
                    is_enabled=integration_type.id in enabled_type_ids,
                )
                for integration_type in integration_types
            ],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )


def _direction_value(direction: DirectionFilter) -> str | None:
    """Return the direction to filter on, or None for all."""
    return None if direction is DirectionFilter.ALL else direction.value
