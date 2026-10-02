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
from app.features.clients.exceptions import ClientNotFoundError
from app.features.clients.models import Client
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientRepository,
)
from app.features.integration_tools.exceptions import (
    IntegrationToolNotFoundError,
    UnknownIntegrationTypesError,
    UnknownToolFlowsError,
)
from app.features.integration_tools.models import (
    IntegrationTool,
    IntegrationToolField,
)
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
    IntegrationToolRepository,
)
from app.features.integration_tools.schemas import (
    ClientToolFilters,
    ConnectorInfo,
    ConnectorParameterResponse,
    FieldScope,
    FlowDescriptionResponse,
    IntegrationToolFieldResponse,
    IntegrationToolFilters,
    IntegrationToolResponse,
    IntegrationToolUpsert,
    IntegrationTypeSummary,
    OperationDescriptionResponse,
    ToolFieldFilters,
    ToolFieldInput,
    ToolFieldsUpdate,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.shared.pagination import MAX_PAGE_SIZE
from app.shared.schemas import Page

logger = logging.getLogger(__name__)


class IntegrationToolService:
    """Lists tools for super admins and for individual clients.

    Attributes:
        session: Unit of work for the request; committed on upsert.
        tools: Persistence for integration tools.
        tool_fields: Field keys entered for tools, global or per client.
        integration_types: Looks up the types a tool serves.
        client_configs: Tells which types a client has enabled.
        clients: Looks up clients for their own field lists.
        registry: Connector code, used to describe each tool's
            arguments and whether it can be connected yet.
    """

    def __init__(
        self,
        session: AsyncSession,
        tools: IntegrationToolRepository,
        tool_fields: IntegrationToolFieldRepository,
        integration_types: IntegrationTypeRepository,
        client_configs: ClientIntegrationConfigRepository,
        clients: ClientRepository,
        registry: ConnectorRegistry,
    ) -> None:
        self.session = session
        self.tools = tools
        self.tool_fields = tool_fields
        self.integration_types = integration_types
        self.client_configs = client_configs
        self.clients = clients
        self.registry = registry

    async def upsert_tool(
        self, code: str, definition: IntegrationToolUpsert
    ) -> IntegrationToolResponse:
        """Create the tool ``code`` or replace its whole definition.

        ``fields``, when sent, replaces the tool's global field list.

        Raises:
            UnknownIntegrationTypesError: A listed type does not exist.
            UnknownToolFlowsError: A field names a flow the tool does not
                have.
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
        if definition.fields is not None:
            self._check_field_flows(tool, definition.fields)
            # A new tool's id is assigned on flush; fields refer to it.
            await self.session.flush()
            await self.tool_fields.replace(
                tool.id, None, self._to_field_rows(definition.fields)
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

    async def list_tool_fields(
        self, tool_code: str, filters: ToolFieldFilters
    ) -> Page[IntegrationToolFieldResponse]:
        """Return a tool's global field keys.

        Raises:
            IntegrationToolNotFoundError: No tool has this code.
        """
        tool = await self._get_tool_or_raise(tool_code)
        return await self._fields_page(tool, [None], filters)

    async def list_tool_fields_for_client(
        self, client: Client, tool_code: str, filters: ToolFieldFilters
    ) -> Page[IntegrationToolFieldResponse]:
        """Return a tool's global fields and the client's own.

        Raises:
            IntegrationToolNotFoundError: The tool does not exist, is
                inactive, or serves none of the client's enabled types.
                All three look the same, so the catalogue is not exposed.
        """
        tool = await self.tools.get_by_code(tool_code)
        enabled_rows = await self.client_configs.list_with_types(
            client.id, usable_only=True
        )
        if (
            tool is None
            or not tool.is_active
            or not any(
                tool.serves(integration_type.id)
                for _, integration_type in enabled_rows
            )
        ):
            raise IntegrationToolNotFoundError(tool_code)
        return await self._fields_page(tool, [None, client.id], filters)

    async def list_client_tool_fields(
        self, client_id: uuid.UUID, tool_code: str, filters: ToolFieldFilters
    ) -> Page[IntegrationToolFieldResponse]:
        """Return the fields added for one client only.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationToolNotFoundError: No tool has this code.
        """
        tool = await self._get_tool_or_raise(tool_code)
        await self._get_client_or_raise(client_id)
        return await self._fields_page(tool, [client_id], filters)

    async def set_client_tool_fields(
        self, client_id: uuid.UUID, tool_code: str, update: ToolFieldsUpdate
    ) -> Page[IntegrationToolFieldResponse]:
        """Replace the fields added for one client, and return them.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationToolNotFoundError: No tool has this code.
            UnknownToolFlowsError: A field names a flow the tool does not
                have.
        """
        tool = await self._get_tool_or_raise(tool_code)
        await self._get_client_or_raise(client_id)
        self._check_field_flows(tool, update.fields)
        await self.tool_fields.replace(
            tool.id, client_id, self._to_field_rows(update.fields)
        )
        await self.session.commit()
        logger.info(
            "Set %s fields of tool %s for client %s",
            len(update.fields),
            tool_code,
            client_id,
        )
        return await self._fields_page(
            tool,
            [client_id],
            ToolFieldFilters(limit=MAX_PAGE_SIZE),
        )

    async def _fields_page(
        self,
        tool: IntegrationTool,
        client_ids: list[uuid.UUID | None],
        filters: ToolFieldFilters,
    ) -> Page[IntegrationToolFieldResponse]:
        fields, total = await self.tool_fields.list_page(
            tool.id,
            client_ids=client_ids,
            flow=filters.flow,
            limit=filters.limit,
            offset=filters.offset,
        )
        return Page[IntegrationToolFieldResponse](
            items=[self._to_field_response(field) for field in fields],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    def _check_field_flows(
        self, tool: IntegrationTool, fields: list[ToolFieldInput]
    ) -> None:
        # Tools without configured flows (Python connectors, or none
        # yet) accept any flow name, since there is nothing to check.
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        flows = (
            connector_class.describe_flows(tool.connector_config)
            if connector_class
            else {}
        )
        if not flows:
            return
        unknown = {field.flow for field in fields} - set(flows)
        if unknown:
            raise UnknownToolFlowsError(tool.code, list(unknown))

    async def _get_tool_or_raise(self, tool_code: str) -> IntegrationTool:
        tool = await self.tools.get_by_code(tool_code)
        if tool is None:
            raise IntegrationToolNotFoundError(tool_code)
        return tool

    async def _get_client_or_raise(self, client_id: uuid.UUID) -> Client:
        client = await self.clients.get_by_id(client_id)
        if client is None:
            raise ClientNotFoundError(client_id)
        return client

    @staticmethod
    def _to_field_rows(
        fields: list[ToolFieldInput],
    ) -> list[IntegrationToolField]:
        return [
            IntegrationToolField(
                flow=field.flow,
                key=field.key,
                label=field.label,
                value_type=field.value_type,
            )
            for field in fields
        ]

    @staticmethod
    def _to_field_response(
        field: IntegrationToolField,
    ) -> IntegrationToolFieldResponse:
        return IntegrationToolFieldResponse(
            id=field.id,
            flow=field.flow,
            key=field.key,
            label=field.label,
            value_type=field.value_type,
            scope=FieldScope.CLIENT if field.client_id else FieldScope.GLOBAL,
            created_at=field.created_at,
            updated_at=field.updated_at,
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
                flows=[
                    FlowDescriptionResponse(
                        name=name,
                        purpose=flow.purpose,
                        description=flow.description,
                        inputs=flow.all_inputs(),
                        outputs=list(flow.outputs),
                    )
                    for name, flow in (
                        connector_class.describe_flows(tool.connector_config)
                        if connector_class
                        else {}
                    ).items()
                ],
            ),
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )
