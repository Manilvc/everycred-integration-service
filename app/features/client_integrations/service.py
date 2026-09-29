"""Business rules for the client admin's Integrations screen.

A client admin sees the tools that serve the integration types a super
admin enabled for them. For each tool they can store credentials, turn
it on or off, and test it. Which tool their users are routed through
for a type stays a super admin choice (``is_default`` on the card).
"""

import asyncio
import logging
import traceback
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    ConnectionRequirements,
    ConnectorContext,
    ConnectorError,
    IntegrationConnector,
    MissingParametersError,
    UnknownOperationError,
)
from app.connectors.registry import ConnectorRegistry
from app.connectors.resolution import connector_kind, resolve_connector
from app.core.config import Settings
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
    ClientIntegrationsResponse,
    ConnectionTestResponse,
    IntegrationGroup,
    IntegrationTypeInfo,
    ToolCard,
    ToolConnectionDetail,
    ToolConnectionUpdate,
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

logger = logging.getLogger(__name__)

_STATUS_TO_CARD = {
    ToolConnectionStatus.NOT_TESTED: CardStatus.NOT_TESTED,
    ToolConnectionStatus.CONFIGURED: CardStatus.CONFIGURED,
    ToolConnectionStatus.CONNECTED: CardStatus.CONNECTED,
    ToolConnectionStatus.FAILED: CardStatus.FAILED,
}


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
    def is_enabled(self) -> bool:
        # Without a row, storing credentials counts as switching it on,
        # which keeps setups made through the super admin API working.
        if self.connection is not None:
            return self.connection.is_enabled
        return self.credential is not None

    @property
    def card_status(self) -> CardStatus:
        if self.connector_class is None:
            return CardStatus.UNAVAILABLE
        if self.connection is not None and not self.connection.is_enabled:
            return CardStatus.DISABLED
        if self.connection is None and self.credential is None:
            return CardStatus.AVAILABLE
        if self.missing_credentials:
            return CardStatus.NEEDS_CREDENTIALS
        if self.connection is None:
            return CardStatus.NOT_TESTED
        return _STATUS_TO_CARD[ToolConnectionStatus(self.connection.status)]


class ClientIntegrationService:
    """Lists, configures, and tests a client's own integration tools."""

    def __init__(
        self,
        session: AsyncSession,
        configs: ClientIntegrationConfigRepository,
        tools: IntegrationToolRepository,
        credentials: ClientToolCredentialRepository,
        connections: ClientToolConnectionRepository,
        secret_store: SecretStore,
        registry: ConnectorRegistry,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.configs = configs
        self.tools = tools
        self.credentials = credentials
        self.connections = connections
        self.secret_store = secret_store
        self.registry = registry
        self.http_client = http_client
        self.settings = settings

    async def list_integrations(
        self, client: Client
    ) -> ClientIntegrationsResponse:
        """Return the client's enabled types, each with its tools."""
        enabled = await self._enabled_types(client)
        tools = await self.tools.list_active_for_types(
            [integration_type.id for _, integration_type in enabled]
        )
        connections = await self.connections.by_tool_for_client(client.id)
        credentials = await self.credentials.by_tool_for_client(client.id)

        groups = []
        for config, integration_type in enabled:
            cards = []
            for tool in tools:
                if not tool.serves(integration_type.id):
                    continue
                state = self._state(
                    tool, connections.get(tool.id), credentials.get(tool.id)
                )
                cards.append(
                    ToolCard(
                        code=tool.code,
                        name=tool.name,
                        provider=tool.provider,
                        status=state.card_status,
                        is_enabled=state.is_enabled,
                        is_default=config.integration_tool_id == tool.id,
                        last_tested_at=(
                            state.connection.last_tested_at
                            if state.connection
                            else None
                        ),
                    )
                )
            groups.append(
                IntegrationGroup(
                    integration_type=IntegrationTypeInfo.model_validate(
                        integration_type
                    ),
                    tools=cards,
                )
            )
        return ClientIntegrationsResponse(groups=groups)

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
            connection = ClientToolConnection(
                client_id=client.id, integration_tool_id=tool.id
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
            or not any(
                tool.serves(integration_type.id)
                for _, integration_type in enabled
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
            integration_types=[
                IntegrationTypeInfo.model_validate(integration_type)
                for _, integration_type in enabled
                if tool.serves(integration_type.id)
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
