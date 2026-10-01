"""Business rules for the client admin's Integrations screen.

A client admin sees the tools that serve the integration types a super
admin enabled for them. For each tool they can store credentials, turn
it on or off, and test it. Which tool their users are routed through
for a type stays a super admin choice (``is_default`` on the card).
"""

import asyncio
import logging
import traceback
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    ConnectionRequirements,
    ConnectorContext,
    ConnectorError,
    IntegrationConnector,
    InvalidInputError,
    MissingParametersError,
    UnknownOperationError,
)
from app.connectors.http.config import FlowConfig
from app.connectors.registry import ConnectorRegistry
from app.connectors.resolution import connector_kind, resolve_connector
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.client_integrations.models import (
    TEST_MESSAGE_MAX_LENGTH,
    ClientToolConnection,
    ToolConnectionStatus,
)
from app.features.client_integrations.repository import (
    ClientToolConnectionRepository,
)
from app.features.client_integrations.schemas import (
    CardStatus,
    ConnectionTestResponse,
    IntegrationGroup,
    IntegrationsListing,
    IntegrationsListingResponse,
    IntegrationSystem,
    IntegrationTypeInfo,
    ToolConnectionDetail,
    ToolConnectionUpdate,
    UserIntegrationGroup,
    UserIntegrationsListing,
    UserIntegrationsListingResponse,
    UserIntegrationStatus,
    UserIntegrationSystem,
)
from app.features.clients.models import (
    Client,
    ClientIntegrationConfig,
    ClientToolCredential,
)
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.clients.service import tool_credentials_secret_name
from app.features.integration_tools.exceptions import (
    IntegrationToolNotFoundError,
)
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_tools.repository import (
    IntegrationToolRepository,
)
from app.features.integration_types.models import IntegrationType
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.user_connections.models import UserIntegrationConnection
from app.features.user_connections.repository import (
    UserConnectionRepository,
)

logger = logging.getLogger(__name__)

_STATUS_TO_CARD = {
    ToolConnectionStatus.NOT_TESTED: CardStatus.NOT_TESTED,
    ToolConnectionStatus.CONFIGURED: CardStatus.CONFIGURED,
    ToolConnectionStatus.CONNECTED: CardStatus.CONNECTED,
    ToolConnectionStatus.FAILED: CardStatus.FAILED,
}


class UnknownFlowsError(AppError):
    """Raised when field mappings name flows the tool does not offer."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "unknown_flows"

    def __init__(self, tool_code: str, flows: list[str]) -> None:
        super().__init__(
            f"Tool '{tool_code}' has no flows named: " + ", ".join(flows)
        )


# Statuses shown as connected (green dot) on the Integrations screen.
# ``configured`` means Test connection confirmed stored credentials for a
# tool that cannot be called without a user.
_CONNECTED_STATUSES = {CardStatus.CONNECTED, CardStatus.CONFIGURED}
BUILT_IN_TEST_MESSAGE = "Built in to EveryCRED; there is nothing to connect."


def group_label(integration_type: IntegrationType) -> str:
    """Return ``"CONFIRM - Identity & verification"`` style labels."""
    name = integration_type.name.upper()
    if integration_type.description:
        return f"{name} - {integration_type.description}"
    return name


@dataclass(frozen=True, slots=True)
class _ToolState:
    """Everything needed to describe one tool for one client."""

    tool: IntegrationTool
    connector_class: type[IntegrationConnector] | None
    requirements: ConnectionRequirements | None
    connection: ClientToolConnection | None
    credential: ClientToolCredential | None

    @property
    def stored_credentials(self) -> list[str]:
        return (
            list(self.credential.credential_names) if self.credential else []
        )

    @property
    def missing_credentials(self) -> list[str]:
        required = (
            self.requirements.required_credentials if self.requirements else []
        )
        return sorted(set(required) - set(self.stored_credentials))

    @property
    def is_built_in(self) -> bool:
        return bool(self.connector_class and self.connector_class.is_built_in)

    @property
    def is_enabled(self) -> bool:
        # Without a row, a tool is in effect on when users could use it:
        # its credentials are stored (setups made through the super
        # admin API), or it needs none. This mirrors the check in
        # user_connections, which blocks only an explicit switch-off.
        if self.connector_class is None:
            return False
        if self.connection is not None:
            return self.connection.is_enabled
        return self.credential is not None or not self.missing_credentials

    @property
    def is_usable_by_users(self) -> bool:
        """Whether the client's users can connect through the tool now."""
        return self.is_enabled and not self.missing_credentials

    @property
    def card_status(self) -> CardStatus:
        if self.connector_class is None:
            return CardStatus.UNAVAILABLE
        if self.connection is not None and not self.connection.is_enabled:
            return CardStatus.DISABLED
        if self.is_built_in:
            return CardStatus.CONNECTED
        if self.connection is None and self.credential is None:
            return CardStatus.AVAILABLE
        if self.missing_credentials:
            return CardStatus.NEEDS_CREDENTIALS
        if self.connection is None:
            return CardStatus.NOT_TESTED
        return _STATUS_TO_CARD[ToolConnectionStatus(self.connection.status)]


@dataclass(frozen=True, slots=True)
class _Catalogue:
    """What both listings are built from, loaded once per request.

    Attributes:
        integration_types: Every active type, in display order.
        config_by_type_id: The client's usable configurations.
        tool_states: Every active tool serving any listed type, with its
            state for the client, in display order.
    """

    integration_types: Sequence[IntegrationType]
    config_by_type_id: dict[uuid.UUID, ClientIntegrationConfig]
    tool_states: list[_ToolState]

    def states_for(
        self, integration_type: IntegrationType
    ) -> list[_ToolState]:
        """Return the tools serving ``integration_type``, in order."""
        return [
            state
            for state in self.tool_states
            if state.tool.serves(integration_type.id)
        ]


class ClientIntegrationService:
    """Lists, configures, and tests a client's own integration tools."""

    def __init__(
        self,
        session: AsyncSession,
        configs: ClientIntegrationConfigRepository,
        integration_types: IntegrationTypeRepository,
        tools: IntegrationToolRepository,
        credentials: ClientToolCredentialRepository,
        connections: ClientToolConnectionRepository,
        user_connections: UserConnectionRepository,
        secret_store: SecretStore,
        registry: ConnectorRegistry,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.configs = configs
        self.integration_types = integration_types
        self.tools = tools
        self.credentials = credentials
        self.connections = connections
        self.user_connections = user_connections
        self.secret_store = secret_store
        self.registry = registry
        self.http_client = http_client
        self.settings = settings

    async def list_integrations(
        self, client: Client
    ) -> IntegrationsListingResponse:
        """Return every active type with the client's systems for it.

        A type the client has not enabled is still listed, so the screen
        always shows the same groups, but only with built-in systems.
        """
        catalogue = await self._load_catalogue(client)
        groups = []
        for integration_type in catalogue.integration_types:
            config = catalogue.config_by_type_id.get(integration_type.id)
            systems = [
                self._to_system(
                    state,
                    integration_type,
                    is_chosen=(
                        config is not None
                        and config.integration_tool_id == state.tool.id
                    ),
                )
                for state in catalogue.states_for(integration_type)
                if config is not None or state.is_built_in
            ]
            groups.append(
                IntegrationGroup(
                    id=integration_type.id,
                    key=integration_type.code,
                    label=group_label(integration_type),
                    description=integration_type.description,
                    systems=systems,
                )
            )
        return IntegrationsListingResponse(
            data=IntegrationsListing(groups=groups)
        )

    async def list_user_integrations(
        self, client: Client, user_uuid: uuid.UUID
    ) -> UserIntegrationsListingResponse:
        """Return every active type with the user's status for each tool.

        A user connects to a type through the tool the client routes it
        to, so each group lists that tool (if the type is enabled and a
        tool is chosen) and any built-in tools. Works for users this
        service has never seen: they are simply not connected yet.
        """
        catalogue = await self._load_catalogue(client)
        connection_by_type_id = {
            connection.integration_type_id: connection
            for connection, _ in await self.user_connections.list_for_user(
                client.id, user_uuid
            )
        }
        groups = []
        for integration_type in catalogue.integration_types:
            config = catalogue.config_by_type_id.get(integration_type.id)
            chosen_tool_id = config.integration_tool_id if config else None
            systems = [
                self._to_user_system(
                    state,
                    integration_type,
                    connection_by_type_id.get(integration_type.id),
                )
                for state in catalogue.states_for(integration_type)
                if state.is_built_in or state.tool.id == chosen_tool_id
            ]
            groups.append(
                UserIntegrationGroup(
                    id=integration_type.id,
                    key=integration_type.code,
                    label=group_label(integration_type),
                    description=integration_type.description,
                    systems=systems,
                )
            )
        return UserIntegrationsListingResponse(
            data=UserIntegrationsListing(user_uuid=user_uuid, groups=groups)
        )

    async def _load_catalogue(self, client: Client) -> _Catalogue:
        integration_types = await self.integration_types.list_active()
        config_by_type_id = {
            integration_type.id: config
            for config, integration_type in await self._enabled_types(client)
        }
        tools = await self.tools.list_active_for_types(
            [integration_type.id for integration_type in integration_types]
        )
        connections = await self.connections.by_tool_for_client(client.id)
        credentials = await self.credentials.by_tool_for_client(client.id)
        return _Catalogue(
            integration_types=integration_types,
            config_by_type_id=config_by_type_id,
            tool_states=[
                self._state(
                    tool, connections.get(tool.id), credentials.get(tool.id)
                )
                for tool in tools
            ],
        )

    async def get_tool(
        self, client: Client, tool_code: str
    ) -> ToolConnectionDetail:
        """Return the drawer for one tool.

        Raises:
            IntegrationToolNotFoundError: The tool does not exist, is
                inactive, or serves none of the client's enabled types.
        """
        tool, enabled = await self._accessible_tool(client, tool_code)
        state = await self._load_state(client, tool)
        return self._to_detail(state, enabled)

    async def update_tool(
        self, client: Client, tool_code: str, update: ToolConnectionUpdate
    ) -> ToolConnectionDetail:
        """Apply Save from the drawer: the switch and/or credentials.

        Changing credentials resets the status to ``not_tested``, since
        the new values have not been checked yet.

        Raises:
            IntegrationToolNotFoundError: The tool is not available.
            SecretStoreError: The secret store could not be written.
        """
        tool, enabled = await self._accessible_tool(client, tool_code)
        connection = await self.connections.get(client.id, tool.id)
        if connection is None:
            connection = ClientToolConnection(
                client_id=client.id, integration_tool_id=tool.id
            )
            self.connections.add(connection)
        if update.is_enabled is not None:
            connection.is_enabled = update.is_enabled
        if update.field_mappings is not None:
            unknown = sorted(
                set(update.field_mappings) - set(self._flows_of(tool))
            )
            if unknown:
                raise UnknownFlowsError(tool.code, unknown)
            connection.field_mappings = update.field_mappings

        credentials_changed = bool(
            update.credentials or update.remove_credentials
        )
        reference_to_delete = None
        if credentials_changed:
            reference_to_delete = await self._merge_credentials(
                client, tool, update
            )
            connection.status = ToolConnectionStatus.NOT_TESTED
            connection.last_test_message = None
        await self.session.commit()

        if reference_to_delete is not None:
            await self._delete_secret_quietly(reference_to_delete, tool.code)
        logger.info(
            "Client %s updated tool %s (enabled=%s, credentials changed=%s)",
            client.id,
            tool.code,
            connection.is_enabled,
            credentials_changed,
        )
        state = await self._load_state(client, tool)
        return self._to_detail(state, enabled)

    async def test_tool(
        self, client: Client, tool_code: str
    ) -> ConnectionTestResponse:
        """Run Test connection and record the outcome.

        Raises:
            IntegrationToolNotFoundError: The tool is not available.
            SecretStoreError: Stored credentials could not be read.
        """
        tool, enabled = await self._accessible_tool(client, tool_code)
        state = await self._load_state(client, tool)
        tested_at = utc_now()

        if state.connector_class is None:
            return ConnectionTestResponse(
                success=False,
                called_provider=False,
                status=CardStatus.UNAVAILABLE,
                message="No connector is installed for this tool yet.",
                tested_at=tested_at,
            )

        if state.is_built_in:
            success, called_provider, message = (
                True,
                False,
                BUILT_IN_TEST_MESSAGE,
            )
        else:
            success, called_provider, message = await self._run_test(
                client, tool, state, enabled
            )
        if success:
            status = (
                ToolConnectionStatus.CONNECTED
                if called_provider
                else ToolConnectionStatus.CONFIGURED
            )
        else:
            status = ToolConnectionStatus.FAILED

        connection = state.connection
        if connection is None:
            # Keep the switch where it effectively was; testing must not
            # turn a tool on that the admin never enabled.
            connection = ClientToolConnection(
                client_id=client.id,
                integration_tool_id=tool.id,
                is_enabled=state.is_enabled,
            )
            self.connections.add(connection)
        connection.status = status
        connection.last_tested_at = tested_at
        connection.last_test_message = message[:TEST_MESSAGE_MAX_LENGTH]
        await self.session.commit()

        logger.info(
            "Client %s tested tool %s: %s", client.id, tool.code, status
        )
        refreshed = await self._load_state(client, tool)
        return ConnectionTestResponse(
            success=success,
            called_provider=called_provider,
            status=refreshed.card_status,
            message=message,
            tested_at=tested_at,
        )

    async def _run_test(
        self,
        client: Client,
        tool: IntegrationTool,
        state: _ToolState,
        enabled: list[tuple[ClientIntegrationConfig, IntegrationType]],
    ) -> tuple[bool, bool, str]:
        connector_class = state.connector_class
        if connector_class is None:
            return False, False, "No connector is installed for this tool yet."
        config, integration_type = self._config_for_tool(tool, enabled)
        credentials = (
            await self.secret_store.read(state.credential.secret_reference)
            if state.credential
            else {}
        )
        connector = connector_class(
            ConnectorContext(
                client_id=client.id,
                user_uuid=None,
                integration_type_code=integration_type.code,
                tool_code=tool.code,
                client_settings=config.settings,
                http_client=self.http_client,
                credentials=credentials,
                tool_config=tool.connector_config,
            )
        )
        try:
            async with asyncio.timeout(
                self.settings.connector_timeout_seconds
            ):
                outcome = await connector.test_connection()
        except UnknownOperationError:
            return False, False, "This tool's connector cannot be tested."
        except MissingParametersError as exc:
            return (
                False,
                False,
                "Missing: " + ", ".join(exc.missing) + ".",
            )
        except InvalidInputError as exc:
            return False, False, f"Invalid settings: {exc.reason}."
        except ConnectorError as exc:
            return False, True, str(exc)
        except TimeoutError:
            return False, True, "The provider did not respond in time."
        except Exception as exc:
            # As for user connections: log the type and frames only,
            # since connector messages may contain credentials.
            logger.error(
                "Test of %s for client %s raised %s:\n%s",
                tool.code,
                client.id,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            return False, True, "The test failed unexpectedly."
        return True, outcome.called_provider, outcome.message

    async def _merge_credentials(
        self,
        client: Client,
        tool: IntegrationTool,
        update: ToolConnectionUpdate,
    ) -> str | None:
        """Write merged credentials; return a reference to delete, if any."""
        credential = await self.credentials.get(client.id, tool.id)
        stored = (
            await self.secret_store.read(credential.secret_reference)
            if credential
            else {}
        )
        merged = {**stored, **(update.credentials or {})}
        for name in update.remove_credentials:
            merged.pop(name, None)

        if not merged:
            if credential is None:
                return None
            reference = credential.secret_reference
            await self.credentials.delete(credential)
            return reference

        if credential is None:
            reference = await self.secret_store.create(
                tool_credentials_secret_name(client.id, tool.code),
                merged,
                tags={
                    "client_id": str(client.id),
                    "tool_code": tool.code,
                    "purpose": "tool-credentials",
                },
            )
            credential = ClientToolCredential(
                client_id=client.id,
                integration_tool_id=tool.id,
                secret_reference=reference,
            )
            self.credentials.add(credential)
        else:
            await self.secret_store.replace(
                credential.secret_reference, merged
            )
        credential.credential_names = sorted(merged)
        credential.updated_at = utc_now()
        return None

    async def _delete_secret_quietly(
        self, reference: str, tool_code: str
    ) -> None:
        try:
            await self.secret_store.delete(reference)
            await self.session.commit()
        except SecretStoreError:
            logger.warning(
                "Credentials for %s removed, but the secret could not be "
                "deleted; it will be reused if stored again",
                tool_code,
            )

    async def _enabled_types(
        self, client: Client
    ) -> list[tuple[ClientIntegrationConfig, IntegrationType]]:
        rows = await self.configs.list_with_types(client.id, usable_only=True)
        return [
            (config, integration_type) for config, integration_type in rows
        ]

    async def _accessible_tool(
        self, client: Client, tool_code: str
    ) -> tuple[
        IntegrationTool, list[tuple[ClientIntegrationConfig, IntegrationType]]
    ]:
        tool = await self.tools.get_by_code(tool_code)
        enabled = await self._enabled_types(client)
        if (
            tool is None
            or not tool.is_active
            or not (
                self._is_built_in(tool)
                or any(
                    tool.serves(integration_type.id)
                    for _, integration_type in enabled
                )
            )
        ):
            # Tools outside the client's scope look the same as missing
            # ones, so the catalogue is not exposed.
            raise IntegrationToolNotFoundError(tool_code)
        return tool, enabled

    async def _load_state(
        self, client: Client, tool: IntegrationTool
    ) -> _ToolState:
        return self._state(
            tool,
            await self.connections.get(client.id, tool.id),
            await self.credentials.get(client.id, tool.id),
        )

    def _state(
        self,
        tool: IntegrationTool,
        connection: ClientToolConnection | None,
        credential: ClientToolCredential | None,
    ) -> _ToolState:
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        requirements = (
            connector_class.describe_requirements(tool.connector_config)
            if connector_class
            else None
        )
        return _ToolState(
            tool, connector_class, requirements, connection, credential
        )

    def _is_built_in(self, tool: IntegrationTool) -> bool:
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        return bool(connector_class and connector_class.is_built_in)

    @staticmethod
    def _to_system(
        state: _ToolState,
        integration_type: IntegrationType,
        *,
        is_chosen: bool,
    ) -> IntegrationSystem:
        status = state.card_status
        return IntegrationSystem(
            id=state.tool.id,
            code=state.tool.code,
            source_role_id=integration_type.id,
            name=state.tool.name,
            status_note=state.tool.description,
            is_connected=status in _CONNECTED_STATUSES,
            is_active=state.is_enabled,
            is_default=state.is_built_in or is_chosen,
            status=status,
            last_tested_at=(
                state.connection.last_tested_at if state.connection else None
            ),
        )

    @staticmethod
    def _to_user_system(
        state: _ToolState,
        integration_type: IntegrationType,
        connection: UserIntegrationConnection | None,
    ) -> UserIntegrationSystem:
        # A built-in tool has no per-user connection to report.
        if state.is_built_in:
            connection = None
        if not state.is_usable_by_users:
            status = UserIntegrationStatus.UNAVAILABLE
        elif state.is_built_in:
            status = UserIntegrationStatus.CONNECTED
        elif connection is None:
            status = UserIntegrationStatus.NOT_CONNECTED
        else:
            status = UserIntegrationStatus(connection.status)
        return UserIntegrationSystem(
            id=state.tool.id,
            code=state.tool.code,
            source_role_id=integration_type.id,
            name=state.tool.name,
            status_note=state.tool.description,
            is_connected=status is UserIntegrationStatus.CONNECTED,
            is_active=state.is_usable_by_users,
            is_default=True,
            status=status,
            tool_status=state.card_status,
            connection_id=connection.id if connection else None,
            last_attempt_at=connection.last_attempt_at if connection else None,
            last_connected_at=(
                connection.last_connected_at if connection else None
            ),
            last_error=connection.last_error if connection else None,
        )

    def _flows_of(self, tool: IntegrationTool) -> dict[str, FlowConfig]:
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        if connector_class is None:
            return {}
        return connector_class.describe_flows(tool.connector_config)

    @staticmethod
    def _config_for_tool(
        tool: IntegrationTool,
        enabled: list[tuple[ClientIntegrationConfig, IntegrationType]],
    ) -> tuple[ClientIntegrationConfig, IntegrationType]:
        # Prefer a type where this tool is the one users are routed
        # through, so the test uses the same settings as real calls.
        serving = [
            (config, integration_type)
            for config, integration_type in enabled
            if tool.serves(integration_type.id)
        ]
        for config, integration_type in serving:
            if config.integration_tool_id == tool.id:
                return config, integration_type
        return serving[0]

    def _to_detail(
        self,
        state: _ToolState,
        enabled: list[tuple[ClientIntegrationConfig, IntegrationType]],
    ) -> ToolConnectionDetail:
        tool = state.tool
        return ToolConnectionDetail(
            code=tool.code,
            name=tool.name,
            provider=tool.provider,
            description=tool.description,
            # Built-in tools are open to every client, so they list all
            # their types, not only the ones enabled for the client.
            integration_types=[
                IntegrationTypeInfo.model_validate(integration_type)
                for integration_type in (
                    tool.integration_types
                    if state.is_built_in
                    else [
                        enabled_type
                        for _, enabled_type in enabled
                        if tool.serves(enabled_type.id)
                    ]
                )
            ],
            connector_kind=(
                connector_kind(state.connector_class)
                if state.connector_class
                else None
            ),
            auth_method=(
                state.requirements.auth_method if state.requirements else None
            ),
            required_credentials=(
                state.requirements.required_credentials
                if state.requirements
                else []
            ),
            stored_credentials=state.stored_credentials,
            missing_credentials=state.missing_credentials,
            can_test=bool(state.requirements and state.requirements.can_test),
            flows=list(self._flows_of(tool)),
            field_mappings=(
                dict(state.connection.field_mappings or {})
                if state.connection
                else {}
            ),
            operations=[
                operation.name
                for operation in (
                    state.connector_class.describe_operations(
                        tool.connector_config
                    )
                    if state.connector_class
                    else []
                )
            ],
            is_enabled=state.is_enabled,
            status=state.card_status,
            last_tested_at=(
                state.connection.last_tested_at if state.connection else None
            ),
            last_test_message=(
                state.connection.last_test_message
                if state.connection
                else None
            ),
        )
