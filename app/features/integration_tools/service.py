"""Business logic for defining and browsing integration tools."""

import logging
import uuid
from collections.abc import Collection

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import describe_parameters
from app.connectors.registry import ConnectorRegistry
from app.connectors.resolution import (
    ConnectorKind,
    connector_kind,
    resolve_connector,
)
from app.features.clients.models import Client
from app.features.clients.repository import ClientIntegrationConfigRepository
from app.features.integration_tools.exceptions import (
    UnknownIntegrationTypesError,
)
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_tools.repository import (
    IntegrationToolRepository,
)
from app.features.integration_tools.schemas import (
    ClientToolFilters,
    ConnectorInfo,
    ConnectorParameterResponse,
    IntegrationToolFilters,
    IntegrationToolResponse,
    IntegrationToolUpsert,
    IntegrationTypeSummary,
    OperationDescriptionResponse,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.shared.schemas import Page

logger = logging.getLogger(__name__)


class IntegrationToolService:
    """Lists tools for super admins and for individual clients.

    Attributes:
        session: Unit of work for the request; committed on upsert.
        tools: Persistence for integration tools.
        integration_types: Looks up the types a tool serves.
        client_configs: Tells which types a client has enabled.
        registry: Connector code, used to describe each tool's
            arguments and whether it can be connected yet.
    """

    def __init__(
        self,
        session: AsyncSession,
        tools: IntegrationToolRepository,
        integration_types: IntegrationTypeRepository,
        client_configs: ClientIntegrationConfigRepository,
        registry: ConnectorRegistry,
    ) -> None:
        self.session = session
        self.tools = tools
        self.integration_types = integration_types
        self.client_configs = client_configs
        self.registry = registry

    async def upsert_tool(
        self, code: str, definition: IntegrationToolUpsert
    ) -> IntegrationToolResponse:
        """Create the tool ``code`` or replace its whole definition.

        Raises:
            UnknownIntegrationTypesError: A listed type does not exist.
        """
        integration_types = await self.integration_types.get_by_codes(
            definition.integration_types
        )
        unknown_codes = set(definition.integration_types) - {
            integration_type.code for integration_type in integration_types
        }
        if unknown_codes:
            raise UnknownIntegrationTypesError(list(unknown_codes))

        tool = await self.tools.get_by_code(code)
        is_new = tool is None
        if tool is None:
            tool = IntegrationTool(code=code)
            self.tools.add(tool)
        tool.name = definition.name
        tool.provider = definition.provider
        tool.description = definition.description
        tool.is_active = definition.is_active
        tool.display_order = definition.display_order
        tool.integration_types = list(integration_types)
        tool.connector_config = (
            definition.connector_config.model_dump(mode="json")
            if definition.connector_config
            else None
        )
        await self.session.commit()
        logger.info(
            "Integration tool %s %s", code, "created" if is_new else "updated"
        )
        return self._to_response(tool)

    async def list_tools(
        self, filters: IntegrationToolFilters
    ) -> Page[IntegrationToolResponse]:
        """Return one page of the whole tool catalogue."""
        tools, total = await self.tools.list_page(
            limit=filters.limit,
            offset=filters.offset,
            integration_type_code=filters.integration_type,
            include_inactive=filters.include_inactive,
        )
        return Page[IntegrationToolResponse](
            items=[self._to_response(tool) for tool in tools],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def list_tools_for_client(
        self, client: Client, filters: ClientToolFilters
    ) -> Page[IntegrationToolResponse]:
        """Return active tools serving the types enabled for ``client``.

        Each tool lists only the client's enabled types, so a client
        never learns which other types exist.
        """
        enabled_rows = await self.client_configs.list_with_types(
            client.id, usable_only=True
        )
        enabled_type_ids = {
            integration_type.id for _, integration_type in enabled_rows
        }
        tools, total = await self.tools.list_page(
            limit=filters.limit,
            offset=filters.offset,
            integration_type_code=filters.integration_type,
            integration_type_ids=enabled_type_ids,
        )
        return Page[IntegrationToolResponse](
            items=[
                self._to_response(tool, visible_type_ids=enabled_type_ids)
                for tool in tools
            ],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    def _to_response(
        self,
        tool: IntegrationTool,
        visible_type_ids: Collection[uuid.UUID] | None = None,
    ) -> IntegrationToolResponse:
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        kind = connector_kind(connector_class) if connector_class else None
        # A Python connector's own connect() signature is what clients
        # must fill; a configured one takes inputs per operation.
        parameters = (
            describe_parameters(connector_class)
            if kind is ConnectorKind.CODE
            else []
        )
        operations = (
            connector_class.describe_operations(tool.connector_config)
            if connector_class
            else []
        )
        integration_types = [
            integration_type
            for integration_type in tool.integration_types
            if visible_type_ids is None
            or integration_type.id in visible_type_ids
        ]
        return IntegrationToolResponse(
            id=tool.id,
            code=tool.code,
            name=tool.name,
            provider=tool.provider,
            description=tool.description,
            is_active=tool.is_active,
            display_order=tool.display_order,
            integration_types=[
                IntegrationTypeSummary.model_validate(integration_type)
                for integration_type in integration_types
            ],
            connector=ConnectorInfo(
                is_available=connector_class is not None,
                kind=kind,
                parameters=[
                    ConnectorParameterResponse.model_validate(parameter)
                    for parameter in parameters
                ],
                operations=[
                    OperationDescriptionResponse.model_validate(operation)
                    for operation in operations
                ],
            ),
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )
