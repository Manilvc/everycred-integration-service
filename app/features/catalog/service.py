"""Turn stored type, tool, and field ids back into display data."""

import uuid

from app.features.catalog.schemas import (
    CatalogResolveRequest,
    CatalogResolveResponse,
    ResolvedField,
    ResolvedTool,
    ResolvedType,
    UnresolvedIds,
)
from app.features.clients.models import Client
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
    IntegrationToolRepository,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)


class CatalogService:
    """Resolves catalogue ids for one client in a single round trip.

    Types and tools are global, so any id resolves, inactive ones
    included: a record that stored the id still needs its name. Fields
    resolve only when global or the calling client's own.
    """

    def __init__(
        self,
        integration_types: IntegrationTypeRepository,
        tools: IntegrationToolRepository,
        fields: IntegrationToolFieldRepository,
    ) -> None:
        self.integration_types = integration_types
        self.tools = tools
        self.fields = fields

    async def resolve(
        self, client: Client, request: CatalogResolveRequest
    ) -> CatalogResolveResponse:
        """Return display data for every id in ``request`` that resolves.

        Duplicate ids are looked up once. Ids with no visible match are
        listed in ``not_found`` instead of raising.
        """
        type_ids = set(request.type_ids)
        tool_ids = set(request.tool_ids)
        field_ids = set(request.field_ids)

        integration_types = await self.integration_types.get_by_ids(type_ids)
        tools = await self.tools.get_by_ids(tool_ids)
        fields = await self.fields.get_by_ids(
            field_ids, client_ids=[None, client.id]
        )

        return CatalogResolveResponse(
            types=[
                ResolvedType.model_validate(row) for row in integration_types
            ],
            tools=[ResolvedTool.model_validate(row) for row in tools],
            fields=[ResolvedField.model_validate(row) for row in fields],
            not_found=UnresolvedIds(
                type_ids=_missing(
                    type_ids, {row.id for row in integration_types}
                ),
                tool_ids=_missing(tool_ids, {row.id for row in tools}),
                field_ids=_missing(field_ids, {row.id for row in fields}),
            ),
        )


def _missing(
    requested: set[uuid.UUID], found: set[uuid.UUID]
) -> list[uuid.UUID]:
    """Return the requested ids absent from ``found``, sorted."""
    return sorted(requested - found)
