"""Business rules for connecting client users to integrations.

A user's inputs (``args`` and ``kwargs``) and the client's credentials
for the chosen tool both live in the secret store; the database keeps
references only. Each call reads what it needs, hands it to the tool's
connector, and forgets it.
"""

import asyncio
import logging
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    ConnectionOutcome,
    ConnectorError,
    IntegrationConnector,
    InvalidInputError,
    MissingParametersError,
    OperationOutcome,
    UnknownOperationError,
    check_arguments_fit,
)
from app.connectors.registry import ConnectorRegistry
from app.connectors.resolution import (
    ConnectorKind,
    connector_kind,
    resolve_connector,
)
from app.core.config import Settings
from app.core.context import get_request_id
from app.core.exceptions import AppError, ConflictError
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.client_integrations.repository import (
    ClientToolConnectionRepository,
)
from app.features.clients.models import Client, ClientIntegrationConfig
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_tools.schemas import IntegrationToolSummary
from app.features.integration_types.models import IntegrationType
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.user_connections.exceptions import (
    ConnectionFailedError,
    ConnectionNotFoundError,
    InvalidConnectionParametersError,
    InvalidInputsError,
    OperationFailedError,
    OperationNotFoundError,
)
from app.features.user_connections.models import (
    LAST_ERROR_MAX_LENGTH,
    ConnectionStatus,
    IntegrationOperationLog,
    UserIntegrationConnection,
)
from app.features.user_connections.repository import (
    UserConnectionRepository,
)
from app.features.user_connections.schemas import (
    ConnectionParameters,
    OperationRequest,
    OperationResultResponse,
    ParameterSummary,
    UserConnectionResponse,
)
from app.features.user_connections.targets import (
    IntegrationTarget,
    IntegrationTargetResolver,
    missing_inputs_error,
)

logger = logging.getLogger(__name__)

UNEXPECTED_CONNECTOR_ERROR = "The integration failed unexpectedly."
TIMEOUT_MESSAGE = "The integration did not respond in time."


def connection_secret_name(
    client_id: uuid.UUID, user_uuid: uuid.UUID, integration_type_code: str
) -> str:
    """Return the secret store name for one user's connection inputs.

    Deterministic, so saving again for the same user and type reuses the
    secret instead of creating another.
    """
    return (
        f"clients/{client_id}/users/{user_uuid}/connections/"
        f"{integration_type_code}"
    )


class UserConnectionService:
    """Stores user connection inputs and runs connectors and operations.

    Attributes:
        session: Unit of work for the request; committed here.
        connections: Persistence for user connections.
        client_configs: The client's per-integration settings.
        integration_types: The integration type catalogue.
        credentials: References to the client's tool credentials.
        registry: Python connectors by tool code.
        secret_store: Holds user inputs and client credentials.
        http_client: Shared client handed to connectors.
        settings: Connector timeout.
    """

    def __init__(
        self,
        session: AsyncSession,
        connections: UserConnectionRepository,
        client_configs: ClientIntegrationConfigRepository,
        integration_types: IntegrationTypeRepository,
        credentials: ClientToolCredentialRepository,
        tool_connections: ClientToolConnectionRepository,
        registry: ConnectorRegistry,
        secret_store: SecretStore,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.connections = connections
        self.client_configs = client_configs
        self.integration_types = integration_types
        self.credentials = credentials
        self.tool_connections = tool_connections
        self.registry = registry
        self.secret_store = secret_store
        self.http_client = http_client
        self.settings = settings
        self.targets = IntegrationTargetResolver(
            integration_types=integration_types,
            client_configs=client_configs,
            tool_connections=tool_connections,
            credentials=credentials,
            secret_store=secret_store,
            registry=registry,
            http_client=http_client,
        )

    async def list_connections(
        self, client: Client, user_uuid: uuid.UUID
    ) -> list[UserConnectionResponse]:
        """Return every connection of one of the client's users."""
        rows = await self.connections.list_for_user(client.id, user_uuid)
        config_rows = await self.client_configs.list_with_types(
            client.id, usable_only=False
        )
        tool_by_type_id = {
            integration_type.id: config.integration_tool
            for config, integration_type in config_rows
        }
        return [
            self._to_response(
                connection,
                integration_type,
                tool_by_type_id.get(integration_type.id),
            )
            for connection, integration_type in rows
        ]

    async def get_connection(
        self, client: Client, user_uuid: uuid.UUID, integration_type_code: str
    ) -> UserConnectionResponse:
        """Return one connection.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            ConnectionNotFoundError: The user has no such connection.
        """
        integration_type = await self._get_integration_type(
            integration_type_code
        )
        connection = await self._get_connection_or_raise(
            client, user_uuid, integration_type
        )
        client_config = await self.client_configs.get(
            client.id, integration_type.id
        )
        tool = client_config.integration_tool if client_config else None
        return self._to_response(connection, integration_type, tool)

    async def save_parameters(
        self,
        client: Client,
        user_uuid: uuid.UUID,
        integration_type_code: str,
        parameters: ConnectionParameters,
    ) -> UserConnectionResponse:
        """Create or replace the user's inputs in the secret store.

        Saving resets the connection to ``pending``: the new inputs have
        not been tried yet.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: The client has not enabled it.
            InvalidConnectionParametersError: The chosen tool has a
                Python connector and the inputs do not fit its signature.
            SecretStoreError: The secret store could not be written.
        """
        integration_type, client_config = await self._get_enabled_integration(
            client, integration_type_code
        )
        tool = client_config.integration_tool
        connector_class = (
            resolve_connector(tool.code, tool.connector_config, self.registry)
            if tool
            else None
        )
        if (
            connector_class is not None
            and connector_kind(connector_class) is ConnectorKind.CODE
        ):
            self._raise_if_arguments_do_not_fit(
                connector_class, parameters.args, parameters.kwargs
            )

        inputs = parameters.model_dump()
        connection = await self.connections.get(
            client.id, user_uuid, integration_type.id
        )
        if connection is None:
            reference = await self.secret_store.create(
                connection_secret_name(
                    client.id, user_uuid, integration_type.code
                ),
                inputs,
                tags={
                    "client_id": str(client.id),
                    "user_uuid": str(user_uuid),
                    "integration_type": integration_type.code,
                    "purpose": "user-connection",
                },
            )
            connection = UserIntegrationConnection(
                client_id=client.id,
                user_uuid=user_uuid,
                integration_type_id=integration_type.id,
                parameters_secret_reference=reference,
            )
            self.connections.add(connection)
        else:
            await self.secret_store.replace(
                connection.parameters_secret_reference, inputs
            )
        connection.parameter_summary = {
            "arg_count": len(parameters.args),
            "kwarg_names": sorted(parameters.kwargs),
        }
        connection.status = ConnectionStatus.PENDING
        connection.connection_details = {}
        connection.last_error = None

        try:
            await self.session.commit()
        except IntegrityError as exc:
            # A parallel request created the same connection first. The
            # secret name is shared, so nothing is orphaned.
            await self.session.rollback()
            raise ConflictError(
                "This connection was changed by another request; retry."
            ) from exc

        logger.info(
            "Saved %s connection inputs for user %s of client %s",
            integration_type_code,
            user_uuid,
            client.id,
        )
        return self._to_response(connection, integration_type, tool)

    async def connect(
        self, client: Client, user_uuid: uuid.UUID, integration_type_code: str
    ) -> UserConnectionResponse:
        """Run the tool's connector with the user's saved inputs.

        The outcome is saved before this returns or raises, so a failed
        attempt is visible later through :meth:`get_connection`.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: The client has not enabled it.
            ConnectionNotFoundError: No inputs were saved yet.
            IntegrationToolNotSelectedError: No tool chosen for the type.
            IntegrationToolInactiveError: The chosen tool is inactive.
            ConnectorNotAvailableError: The tool has no connector yet.
            InvalidConnectionParametersError: Saved inputs do not fit.
            MissingInputsError: The connector needs inputs not saved.
            ToolCredentialsMissingError: Credentials are not stored.
            ConnectionFailedError: The connector failed or timed out.
        """
        target = await self._get_callable(client, integration_type_code)
        connection = await self._get_connection_or_raise(
            client, user_uuid, target.integration_type
        )
        inputs = await self.secret_store.read(
            connection.parameters_secret_reference
        )
        args, kwargs = inputs["args"], inputs["kwargs"]
        if connector_kind(target.connector_class) is ConnectorKind.CODE:
            # The connector may have changed since the inputs were saved.
            self._raise_if_arguments_do_not_fit(
                target.connector_class, args, kwargs
            )

        connector = await self._build_connector(target, client, user_uuid)
        connection.last_attempt_at = utc_now()
        failure_message = await self._run_connector(
            connector.connect, args, kwargs, connection, target.tool.code
        )
        await self.session.commit()

        if failure_message is not None:
            raise ConnectionFailedError(failure_message)
        return self._to_response(
            connection, target.integration_type, target.tool
        )

    async def run_operation(
        self,
        client: Client,
        user_uuid: uuid.UUID,
        integration_type_code: str,
        operation: str,
        request: OperationRequest,
    ) -> OperationResultResponse:
        """Run a named operation of the tool for one of the client's users.

        Inputs are the user's saved ones with the request's ``kwargs``
        merged on top; ``args`` in the request replace the saved ones.
        Every call, successful or not, leaves an audit record without
        inputs or results.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: The client has not enabled it.
            ConnectionNotFoundError: The user has no saved connection.
            IntegrationToolNotSelectedError: No tool chosen for the type.
            IntegrationToolInactiveError: The chosen tool is inactive.
            ConnectorNotAvailableError: The tool has no connector yet.
            OperationNotFoundError: The tool has no such operation.
            MissingInputsError: Required inputs were not supplied.
            ToolCredentialsMissingError: Credentials are not stored.
            OperationFailedError: The provider failed or timed out.
        """
        target = await self._get_callable(client, integration_type_code)
        connection = await self._get_connection_or_raise(
            client, user_uuid, target.integration_type
        )
        saved = await self.secret_store.read(
            connection.parameters_secret_reference
        )
        args = (
            request.args
            if "args" in request.model_fields_set
            else saved["args"]
        )
        kwargs = {**saved["kwargs"], **request.kwargs}
        connector = await self._build_connector(target, client, user_uuid)

        started_at = time.perf_counter()
        outcome: OperationOutcome | None = None
        failure: AppError | None = None
        try:
            async with asyncio.timeout(
                self.settings.connector_timeout_seconds
            ):
                outcome = await connector.run_operation(
                    operation, *args, **kwargs
                )
        except UnknownOperationError:
            failure = OperationNotFoundError(target.tool.code, operation)
        except MissingParametersError as exc:
            failure = self._missing_inputs_error(target.tool.code, exc)
        except InvalidInputError as exc:
            failure = InvalidInputsError(exc.names, exc.reason)
        except ConnectorError as exc:
            failure = OperationFailedError(str(exc)[:LAST_ERROR_MAX_LENGTH])
        except TimeoutError:
            failure = OperationFailedError(TIMEOUT_MESSAGE)
        except Exception as exc:
            # Same rule as for connect: never log a connector's exception
            # message, which may contain the inputs or credentials.
            logger.error(
                "Operation %s of %s raised %s:\n%s",
                operation,
                target.tool.code,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            failure = OperationFailedError(UNEXPECTED_CONNECTOR_ERROR)

        self._record_operation(
            client=client,
            user_uuid=user_uuid,
            target=target,
            operation=operation,
            outcome=outcome,
            failure=failure,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        await self.session.commit()

        if failure is not None or outcome is None:
            raise failure or OperationFailedError(UNEXPECTED_CONNECTOR_ERROR)
        return OperationResultResponse(
            operation=operation,
            integration_type_code=target.integration_type.code,
            tool_code=target.tool.code,
            success=outcome.success,
            provider_status_code=outcome.status_code,
            data=outcome.data,
            message=outcome.message,
        )

    async def delete_connection(
        self, client: Client, user_uuid: uuid.UUID, integration_type_code: str
    ) -> None:
        """Remove the connection, then schedule its secret's deletion.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            ConnectionNotFoundError: The user has no such connection.
        """
        integration_type = await self._get_integration_type(
            integration_type_code
        )
        connection = await self._get_connection_or_raise(
            client, user_uuid, integration_type
        )
        reference = connection.parameters_secret_reference
        await self.connections.delete(connection)
        await self.session.commit()
        try:
            await self.secret_store.delete(reference)
            await self.session.commit()
        except SecretStoreError:
            logger.warning(
                "Connection %s/%s/%s removed, but its secret could not be "
                "deleted; it will be reused if saved again",
                client.id,
                user_uuid,
                integration_type_code,
            )
        logger.info(
            "Deleted %s connection for user %s of client %s",
            integration_type_code,
            user_uuid,
            client.id,
        )

    @property
    def _timeout_seconds(self) -> float:
        return self.settings.connector_timeout_seconds

    async def _build_connector(
        self,
        target: IntegrationTarget,
        client: Client,
        user_uuid: uuid.UUID,
    ) -> IntegrationConnector:
        return await self.targets.build_connector(target, client, user_uuid)

    async def _run_connector(
        self,
        connect: Callable[..., Awaitable[ConnectionOutcome]],
        args: list[Any],
        kwargs: dict[str, Any],
        connection: UserIntegrationConnection,
        tool_code: str,
    ) -> str | None:
        """Call the connector and record the outcome on ``connection``.

        Returns:
            None on success, otherwise the safe failure message.

        Raises:
            MissingInputsError: The connector reported missing inputs.
            ToolCredentialsMissingError: It reported missing credentials.
        """
        try:
            async with asyncio.timeout(
                self.settings.connector_timeout_seconds
            ):
                outcome = await connect(*args, **kwargs)
        except MissingParametersError as exc:
            # A setup problem, not a provider failure: nothing to record.
            raise self._missing_inputs_error(tool_code, exc) from exc
        except InvalidInputError as exc:
            raise InvalidInputsError(exc.names, exc.reason) from exc
        except ConnectorError as exc:
            failure_message = str(exc)[:LAST_ERROR_MAX_LENGTH]
            logger.warning(
                "Connector for connection %s failed: %s",
                connection.id,
                failure_message,
            )
        except TimeoutError:
            failure_message = TIMEOUT_MESSAGE
            logger.warning(
                "Connector for connection %s timed out", connection.id
            )
        except Exception as exc:
            # Connectors are plug-in code; any bug in one must end as a
            # recorded failure, not a 500 that loses the attempt. Their
            # exception messages may embed credentials, so only the type
            # and stack frames are logged, never the message itself.
            failure_message = UNEXPECTED_CONNECTOR_ERROR
            logger.error(
                "Connector for connection %s raised %s:\n%s",
                connection.id,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
        else:
            connection.status = ConnectionStatus.CONNECTED
            connection.connection_details = outcome.details
            connection.last_error = None
            connection.last_connected_at = connection.last_attempt_at
            logger.info("Connection %s connected", connection.id)
            return None

        connection.status = ConnectionStatus.FAILED
        connection.last_error = failure_message
        return failure_message

    def _record_operation(
        self,
        *,
        client: Client,
        user_uuid: uuid.UUID,
        target: IntegrationTarget,
        operation: str,
        outcome: OperationOutcome | None,
        failure: AppError | None,
        duration_ms: int,
    ) -> None:
        self.session.add(
            IntegrationOperationLog(
                client_id=client.id,
                user_uuid=user_uuid,
                integration_type_code=target.integration_type.code,
                tool_code=target.tool.code,
                operation=operation[:64],
                succeeded=bool(outcome and outcome.success),
                provider_status_code=outcome.status_code if outcome else None,
                error_code=failure.error_code if failure else None,
                duration_ms=duration_ms,
                request_id=get_request_id(),
            )
        )
        logger.info(
            "Operation %s via %s for user %s of client %s: %s in %s ms",
            operation,
            target.tool.code,
            user_uuid,
            client.id,
            failure.error_code
            if failure
            else ("success" if outcome and outcome.success else "rejected"),
            duration_ms,
        )

    @staticmethod
    def _missing_inputs_error(
        tool_code: str, exc: MissingParametersError
    ) -> AppError:
        return missing_inputs_error(tool_code, exc)

    def _raise_if_arguments_do_not_fit(
        self,
        connector_class: type[IntegrationConnector],
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> None:
        mismatch = check_arguments_fit(connector_class, args, kwargs)
        if mismatch is not None:
            raise InvalidConnectionParametersError(
                f"Parameters do not match the connector: {mismatch}."
            )

    async def _get_integration_type(self, code: str) -> IntegrationType:
        return await self.targets.integration_type(code)

    async def _get_enabled_integration(
        self, client: Client, code: str
    ) -> tuple[IntegrationType, ClientIntegrationConfig]:
        return await self.targets.enabled_integration(client, code)

    async def _get_callable(
        self, client: Client, code: str
    ) -> IntegrationTarget:
        return await self.targets.resolve(client, code)

    async def _get_connection_or_raise(
        self,
        client: Client,
        user_uuid: uuid.UUID,
        integration_type: IntegrationType,
    ) -> UserIntegrationConnection:
        connection = await self.connections.get(
            client.id, user_uuid, integration_type.id
        )
        if connection is None:
            raise ConnectionNotFoundError(integration_type.code)
        return connection

    def _to_response(
        self,
        connection: UserIntegrationConnection,
        integration_type: IntegrationType,
        tool: IntegrationTool | None,
    ) -> UserConnectionResponse:
        summary = connection.parameter_summary or {}
        return UserConnectionResponse(
            id=connection.id,
            client_id=connection.client_id,
            user_uuid=connection.user_uuid,
            integration_type_code=integration_type.code,
            integration_type_name=integration_type.name,
            integration_tool=(
                IntegrationToolSummary.model_validate(tool) if tool else None
            ),
            status=ConnectionStatus(connection.status),
            parameters=ParameterSummary(
                arg_count=summary.get("arg_count", 0),
                kwarg_names=summary.get("kwarg_names", []),
            ),
            connection_details=connection.connection_details,
            last_error=connection.last_error,
            last_attempt_at=connection.last_attempt_at,
            last_connected_at=connection.last_connected_at,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        )
