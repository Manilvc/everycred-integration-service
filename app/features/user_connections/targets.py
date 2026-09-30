"""Find the tool a client uses for an integration type, ready to call.

User connections and verification/gather sessions both need the same
chain of checks: the type exists and is enabled for the client, a tool
is chosen, the tool is active and not switched off by the client, and
it has a connector. This module owns that chain, and builds the
connector with the client's credentials from the secret store.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorContext,
    IntegrationConnector,
    MissingParametersError,
)
from app.connectors.registry import ConnectorRegistry
from app.connectors.resolution import resolve_connector
from app.core.exceptions import AppError
from app.core.secret_store import SecretStore
from app.features.client_integrations.repository import (
    ClientToolConnectionRepository,
)
from app.features.clients.exceptions import IntegrationNotEnabledError
from app.features.clients.models import Client, ClientIntegrationConfig
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_types.exceptions import (
    IntegrationTypeNotFoundError,
)
from app.features.integration_types.models import IntegrationType
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.user_connections.exceptions import (
    ConnectorNotAvailableError,
    IntegrationToolDisabledError,
    IntegrationToolInactiveError,
    IntegrationToolNotSelectedError,
    MissingInputsError,
    ToolCredentialsMissingError,
)


@dataclass(frozen=True, slots=True)
class IntegrationTarget:
    """Everything needed to call a tool for one client."""

    integration_type: IntegrationType
    client_config: ClientIntegrationConfig
    tool: IntegrationTool
    connector_class: type[IntegrationConnector]


class IntegrationTargetResolver:
    """Resolves and builds the connector a client uses for a type."""

    def __init__(
        self,
        integration_types: IntegrationTypeRepository,
        client_configs: ClientIntegrationConfigRepository,
        tool_connections: ClientToolConnectionRepository,
        credentials: ClientToolCredentialRepository,
        secret_store: SecretStore,
        registry: ConnectorRegistry,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.integration_types = integration_types
        self.client_configs = client_configs
        self.tool_connections = tool_connections
        self.credentials = credentials
        self.secret_store = secret_store
        self.registry = registry
        self.http_client = http_client

    async def integration_type(self, code: str) -> IntegrationType:
        """Return the type with ``code``.

        Raises:
            IntegrationTypeNotFoundError: No type has this code.
        """
        integration_type = await self.integration_types.get_by_code(code)
        if integration_type is None:
            raise IntegrationTypeNotFoundError(code)
        return integration_type

    async def enabled_integration(
        self, client: Client, code: str
    ) -> tuple[IntegrationType, ClientIntegrationConfig]:
        """Return the type and the client's config, if enabled for it.

        Raises:
            IntegrationTypeNotFoundError: No type has this code.
            IntegrationNotEnabledError: Not enabled for the client, or
                the type is inactive.
        """
        integration_type = await self.integration_type(code)
        client_config = await self.client_configs.get(
            client.id, integration_type.id
        )
        if (
            client_config is None
            or not client_config.is_enabled
            or not integration_type.is_active
        ):
            raise IntegrationNotEnabledError(code)
        return integration_type, client_config

    async def resolve(self, client: Client, code: str) -> IntegrationTarget:
        """Return the callable tool the client uses for type ``code``.

        Raises:
            IntegrationTypeNotFoundError: No type has this code.
            IntegrationNotEnabledError: The type is not enabled.
            IntegrationToolNotSelectedError: No tool chosen for it.
            IntegrationToolInactiveError: The tool is inactive.
            IntegrationToolDisabledError: The client switched it off.
            ConnectorNotAvailableError: The tool has no connector.
        """
        integration_type, client_config = await self.enabled_integration(
            client, code
        )
        tool = client_config.integration_tool
        if tool is None:
            raise IntegrationToolNotSelectedError(code)
        if not tool.is_active:
            raise IntegrationToolInactiveError(tool.code)
        tool_connection = await self.tool_connections.get(client.id, tool.id)
        if tool_connection is not None and not tool_connection.is_enabled:
            raise IntegrationToolDisabledError(tool.code)
        connector_class = resolve_connector(
            tool.code, tool.connector_config, self.registry
        )
        if connector_class is None:
            raise ConnectorNotAvailableError(tool.code)
        return IntegrationTarget(
            integration_type, client_config, tool, connector_class
        )

    async def build_connector(
        self,
        target: IntegrationTarget,
        client: Client,
        user_uuid: uuid.UUID | None,
        *,
        credentials: dict[str, Any] | None = None,
        session_values: dict[str, Any] | None = None,
    ) -> IntegrationConnector:
        """Instantiate the connector with the client's credentials.

        Args:
            target: The resolved tool.
            client: Client the call is for.
            user_uuid: The client's user, if the call is for one.
            credentials: Already-loaded credentials, to avoid reading the
                secret store again for every step of a flow.
            session_values: Values captured by earlier flow steps.
        """
        if credentials is None:
            credentials = await self.load_credentials(client, target.tool)
        return target.connector_class(
            ConnectorContext(
                client_id=client.id,
                user_uuid=user_uuid,
                integration_type_code=target.integration_type.code,
                tool_code=target.tool.code,
                client_settings=target.client_config.settings,
                http_client=self.http_client,
                credentials=credentials,
                tool_config=target.tool.connector_config,
                session_values=session_values or {},
            )
        )

    async def load_credentials(
        self, client: Client, tool: IntegrationTool
    ) -> dict[str, Any]:
        """Read the client's credentials for ``tool`` (empty if none)."""
        credential = await self.credentials.get(client.id, tool.id)
        if credential is None:
            return {}
        return await self.secret_store.read(credential.secret_reference)


def missing_inputs_error(
    tool_code: str, exc: MissingParametersError
) -> AppError:
    """Turn missing placeholders into the error the client should see.

    Missing credentials are a setup problem for an admin (409); missing
    inputs are the caller's to fix (422).
    """
    missing_credentials = [
        name.removeprefix("credentials.")
        for name in exc.missing
        if name.startswith("credentials.")
    ]
    if missing_credentials:
        return ToolCredentialsMissingError(tool_code, missing_credentials)
    return MissingInputsError(exc.missing)
