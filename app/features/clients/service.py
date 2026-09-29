"""Business rules for clients, their API keys, and their settings."""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import (
    extract_api_key_prefix,
    generate_api_key,
    is_api_key_valid,
)
from app.core.config import Settings
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.clients.exceptions import (
    ApiKeyNotFoundError,
    ClientCodeTakenError,
    ClientNotFoundError,
    IntegrationNotEnabledError,
    InvalidApiKeyError,
    ToolCredentialsNotFoundError,
)
from app.features.clients.models import (
    Client,
    ClientApiKey,
    ClientIntegrationConfig,
    ClientToolCredential,
)
from app.features.clients.repository import (
    ClientApiKeyRepository,
    ClientIntegrationConfigRepository,
    ClientRepository,
    ClientToolCredentialRepository,
)
from app.features.clients.schemas import (
    ApiKeyCreate,
    ClientConfigurationResponse,
    ClientCreate,
    ClientFilters,
    ClientResponse,
    ClientSettingsUpdate,
    ClientSummary,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
    ToolCredentialsResponse,
    ToolCredentialsUpdate,
)
from app.features.integration_tools.exceptions import (
    IntegrationToolNotFoundError,
    IntegrationToolNotUsableError,
)
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_tools.repository import (
    IntegrationToolRepository,
)
from app.features.integration_tools.schemas import IntegrationToolSummary
from app.features.integration_types.exceptions import (
    IntegrationTypeNotFoundError,
)
from app.features.integration_types.models import IntegrationType
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.super_admins.models import SuperAdmin
from app.shared.schemas import Page

logger = logging.getLogger(__name__)

# Writing last_used_at on every request would turn each read into a
# write; recording it at most this often is precise enough for audits.
LAST_USED_UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    """A stored key together with its one-time plaintext value."""

    record: ClientApiKey
    plaintext: str


async def _get_client_or_raise(
    clients: ClientRepository, client_id: uuid.UUID
) -> Client:
    client = await clients.get_by_id(client_id)
    if client is None:
        raise ClientNotFoundError(client_id)
    return client


def _to_config_response(
    config: ClientIntegrationConfig, integration_type: IntegrationType
) -> IntegrationConfigResponse:
    tool = config.integration_tool
    return IntegrationConfigResponse(
        integration_type_code=integration_type.code,
        integration_type_name=integration_type.name,
        integration_tool=(
            IntegrationToolSummary.model_validate(tool) if tool else None
        ),
        is_enabled=config.is_enabled,
        settings=config.settings,
        updated_at=config.updated_at,
    )


class ClientService:
    """Creates and looks up client projects."""

    def __init__(
        self, session: AsyncSession, clients: ClientRepository
    ) -> None:
        self.session = session
        self.clients = clients

    async def create_client(
        self, details: ClientCreate, created_by: SuperAdmin
    ) -> Client:
        """Register a client project.

        Raises:
            ClientCodeTakenError: Another client already uses the code.
        """
        if await self.clients.get_by_code(details.code):
            raise ClientCodeTakenError(details.code)

        client = Client(
            code=details.code,
            name=details.name,
            description=details.description,
            created_by_id=created_by.id,
        )
        self.clients.add(client)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ClientCodeTakenError(details.code) from exc

        logger.info(
            "Client %s (%s) created by super admin %s",
            client.id,
            client.code,
            created_by.id,
        )
        return client

    async def list_clients(
        self, filters: ClientFilters
    ) -> Page[ClientResponse]:
        """Return one page of clients."""
        clients, total = await self.clients.list_page(
            include_inactive=filters.include_inactive,
            limit=filters.limit,
            offset=filters.offset,
        )
        return Page[ClientResponse](
            items=[
                ClientResponse.model_validate(client) for client in clients
            ],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def get_client(self, client_id: uuid.UUID) -> Client:
        """Return a client by id.

        Raises:
            ClientNotFoundError: No client has this id.
        """
        return await _get_client_or_raise(self.clients, client_id)


class ApiKeyService:
    """Issues, revokes, and authenticates client API keys."""

    def __init__(
        self,
        session: AsyncSession,
        clients: ClientRepository,
        api_keys: ClientApiKeyRepository,
        settings: Settings,
    ) -> None:
        self.session = session
        self.clients = clients
        self.api_keys = api_keys
        self.settings = settings

    async def issue_api_key(
        self,
        client_id: uuid.UUID,
        options: ApiKeyCreate,
        issued_by: SuperAdmin,
    ) -> IssuedApiKey:
        """Create a new API key for a client.

        The returned plaintext is the only copy of the key; it cannot be
        recovered later.

        Raises:
            ClientNotFoundError: No client has this id.
        """
        client = await _get_client_or_raise(self.clients, client_id)
        generated_key = generate_api_key(self.settings)
        api_key = ClientApiKey(
            client_id=client.id,
            name=options.name,
            key_prefix=generated_key.prefix,
            key_hash=generated_key.key_hash,
            expires_at=options.expires_at,
            created_by_id=issued_by.id,
        )
        self.api_keys.add(api_key)
        await self.session.commit()

        logger.info(
            "API key %s (prefix %s) issued for client %s by super admin %s",
            api_key.id,
            api_key.key_prefix,
            client.id,
            issued_by.id,
        )
        return IssuedApiKey(record=api_key, plaintext=generated_key.plaintext)

    async def list_api_keys(
        self, client_id: uuid.UUID
    ) -> Sequence[ClientApiKey]:
        """Return every key of a client, including revoked ones.

        Raises:
            ClientNotFoundError: No client has this id.
        """
        await _get_client_or_raise(self.clients, client_id)
        return await self.api_keys.list_for_client(client_id)

    async def revoke_api_key(
        self, client_id: uuid.UUID, api_key_id: uuid.UUID
    ) -> ClientApiKey:
        """Permanently disable a key. Revoking twice is harmless.

        Raises:
            ClientNotFoundError: No client has this id.
            ApiKeyNotFoundError: The key does not belong to the client.
        """
        await _get_client_or_raise(self.clients, client_id)
        api_key = await self.api_keys.get_for_client(api_key_id, client_id)
        if api_key is None:
            raise ApiKeyNotFoundError(api_key_id)

        if api_key.revoked_at is None:
            api_key.revoked_at = utc_now()
            await self.session.commit()
            logger.info(
                "API key %s of client %s revoked", api_key.id, client_id
            )
        return api_key

    async def authenticate(self, presented_key: str) -> Client:
        """Return the active client that owns ``presented_key``.

        Raises:
            InvalidApiKeyError: The key is malformed, unknown, revoked,
                or expired, or its client is inactive.
        """
        key_prefix = extract_api_key_prefix(presented_key)
        if key_prefix is None:
            raise InvalidApiKeyError

        api_key = await self.api_keys.get_by_prefix(key_prefix)
        if api_key is None or not is_api_key_valid(
            presented_key, api_key.key_hash, self.settings
        ):
            raise InvalidApiKeyError

        now = utc_now()
        if not api_key.is_usable(now):
            logger.warning(
                "Rejected revoked or expired API key %s", api_key.key_prefix
            )
            raise InvalidApiKeyError

        client = await self.clients.get_by_id(api_key.client_id)
        if client is None or not client.is_active:
            logger.warning(
                "Rejected API key %s of inactive client %s",
                api_key.key_prefix,
                api_key.client_id,
            )
            raise InvalidApiKeyError

        if (
            api_key.last_used_at is None
            or now - api_key.last_used_at >= LAST_USED_UPDATE_INTERVAL
        ):
            api_key.last_used_at = now
            await self.session.commit()
        return client


class ClientConfigurationService:
    """Reads and updates each client's integration settings."""

    def __init__(
        self,
        session: AsyncSession,
        clients: ClientRepository,
        configs: ClientIntegrationConfigRepository,
        integration_types: IntegrationTypeRepository,
        integration_tools: IntegrationToolRepository,
    ) -> None:
        self.session = session
        self.clients = clients
        self.configs = configs
        self.integration_types = integration_types
        self.integration_tools = integration_tools

    async def list_configs(
        self, client_id: uuid.UUID
    ) -> list[IntegrationConfigResponse]:
        """Return every configuration of a client, enabled or not.

        Raises:
            ClientNotFoundError: No client has this id.
        """
        await _get_client_or_raise(self.clients, client_id)
        rows = await self.configs.list_with_types(client_id, usable_only=False)
        return [
            _to_config_response(config, integration_type)
            for config, integration_type in rows
        ]

    async def set_config(
        self,
        client_id: uuid.UUID,
        integration_type_code: str,
        update: IntegrationConfigUpdate,
    ) -> IntegrationConfigResponse:
        """Create or replace a client's configuration for one type.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationTypeNotFoundError: No type has this code.
            IntegrationToolNotFoundError: No tool has ``tool_code``.
            IntegrationToolNotUsableError: The tool does not serve this
                type or is inactive.
        """
        await _get_client_or_raise(self.clients, client_id)
        integration_type = await self.integration_types.get_by_code(
            integration_type_code
        )
        if integration_type is None:
            raise IntegrationTypeNotFoundError(integration_type_code)
        tool = await self._resolve_tool(update.tool_code, integration_type)

        config = await self.configs.get(client_id, integration_type.id)
        if config is None:
            config = ClientIntegrationConfig(
                client_id=client_id,
                integration_type_id=integration_type.id,
            )
            self.configs.add(config)
        config.is_enabled = update.is_enabled
        config.integration_tool = tool
        config.settings = update.settings
        await self.session.commit()

        logger.info(
            "Client %s configuration for %s updated (enabled=%s, tool=%s)",
            client_id,
            integration_type.code,
            update.is_enabled,
            tool.code if tool else None,
        )
        return _to_config_response(config, integration_type)

    async def update_own_settings(
        self,
        client: Client,
        integration_type_code: str,
        update: ClientSettingsUpdate,
    ) -> IntegrationConfigResponse:
        """Replace a client's settings for a type it has been given.

        Only ``settings`` change; whether the type is enabled and which
        tool it uses stay under super admin control.

        Raises:
            IntegrationTypeNotFoundError: No type has this code.
            IntegrationNotEnabledError: The type is not enabled for the
                client, or the type is inactive.
        """
        integration_type = await self.integration_types.get_by_code(
            integration_type_code
        )
        if integration_type is None:
            raise IntegrationTypeNotFoundError(integration_type_code)

        config = await self.configs.get(client.id, integration_type.id)
        if (
            config is None
            or not config.is_enabled
            or not integration_type.is_active
        ):
            raise IntegrationNotEnabledError(integration_type_code)

        config.settings = update.settings
        await self.session.commit()
        logger.info(
            "Client %s updated its own %s settings",
            client.id,
            integration_type.code,
        )
        return _to_config_response(config, integration_type)

    async def _resolve_tool(
        self, tool_code: str | None, integration_type: IntegrationType
    ) -> IntegrationTool | None:
        if tool_code is None:
            return None
        tool = await self.integration_tools.get_by_code(tool_code)
        if tool is None:
            raise IntegrationToolNotFoundError(tool_code)
        if not tool.serves(integration_type.id):
            raise IntegrationToolNotUsableError(
                f"Tool '{tool_code}' does not serve integration type "
                f"'{integration_type.code}'."
            )
        if not tool.is_active:
            raise IntegrationToolNotUsableError(
                f"Tool '{tool_code}' is not active."
            )
        return tool

    async def get_client_configuration(
        self, client: Client
    ) -> ClientConfigurationResponse:
        """Return what a client sees about its own setup.

        Only enabled configurations of active integration types are
        included.
        """
        rows = await self.configs.list_with_types(client.id, usable_only=True)
        return ClientConfigurationResponse(
            client=ClientSummary.model_validate(client),
            integrations=[
                _to_config_response(config, integration_type)
                for config, integration_type in rows
            ],
        )


def tool_credentials_secret_name(client_id: uuid.UUID, tool_code: str) -> str:
    """Return the secret store name for a client's tool credentials.

    The name is deterministic, so storing credentials twice for the same
    client and tool reuses one secret instead of leaving orphans.
    """
    return f"clients/{client_id}/tools/{tool_code}"


class ClientCredentialService:
    """Stores each client's credentials for integration tools.

    Values live only in the secret store. The database row records the
    secret's reference and the credential names, so admins can see
    what is configured without anyone reading the values back.
    """

    def __init__(
        self,
        session: AsyncSession,
        clients: ClientRepository,
        tools: IntegrationToolRepository,
        credentials: ClientToolCredentialRepository,
        secret_store: SecretStore,
    ) -> None:
        self.session = session
        self.clients = clients
        self.tools = tools
        self.credentials = credentials
        self.secret_store = secret_store

    async def set_credentials(
        self,
        client_id: uuid.UUID,
        tool_code: str,
        update: ToolCredentialsUpdate,
    ) -> ToolCredentialsResponse:
        """Create or replace a client's credentials for a tool.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationToolNotFoundError: No tool has this code.
            SecretStoreError: The secret store could not be written.
        """
        await _get_client_or_raise(self.clients, client_id)
        tool = await self._get_tool_or_raise(tool_code)

        credential = await self.credentials.get(client_id, tool.id)
        if credential is None:
            reference = await self.secret_store.create(
                tool_credentials_secret_name(client_id, tool.code),
                update.credentials,
                tags={
                    "client_id": str(client_id),
                    "tool_code": tool.code,
                    "purpose": "tool-credentials",
                },
            )
            credential = ClientToolCredential(
                client_id=client_id,
                integration_tool_id=tool.id,
                secret_reference=reference,
            )
            self.credentials.add(credential)
        else:
            await self.secret_store.replace(
                credential.secret_reference, update.credentials
            )
        credential.credential_names = sorted(update.credentials)
        # Touch the row so updated_at records the rotation even when the
        # names are unchanged.
        credential.updated_at = utc_now()
        await self.session.commit()

        logger.info(
            "Stored %s credentials for client %s (%s)",
            tool.code,
            client_id,
            ", ".join(credential.credential_names),
        )
        return self._to_response(credential, tool.code)

    async def get_credentials(
        self, client_id: uuid.UUID, tool_code: str
    ) -> ToolCredentialsResponse:
        """Describe a client's stored credentials without their values.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationToolNotFoundError: No tool has this code.
            ToolCredentialsNotFoundError: Nothing is stored yet.
        """
        await _get_client_or_raise(self.clients, client_id)
        tool = await self._get_tool_or_raise(tool_code)
        credential = await self.credentials.get(client_id, tool.id)
        if credential is None:
            raise ToolCredentialsNotFoundError(tool_code)
        return self._to_response(credential, tool.code)

    async def delete_credentials(
        self, client_id: uuid.UUID, tool_code: str
    ) -> None:
        """Remove the reference, then schedule the secret's deletion.

        The database row goes first: a secret left behind by a failed
        AWS call is harmless and reused on the next save, whereas a row
        pointing at a deleted secret would break every connection.

        Raises:
            ClientNotFoundError: No client has this id.
            IntegrationToolNotFoundError: No tool has this code.
            ToolCredentialsNotFoundError: Nothing is stored.
        """
        await _get_client_or_raise(self.clients, client_id)
        tool = await self._get_tool_or_raise(tool_code)
        credential = await self.credentials.get(client_id, tool.id)
        if credential is None:
            raise ToolCredentialsNotFoundError(tool_code)

        reference = credential.secret_reference
        await self.credentials.delete(credential)
        await self.session.commit()
        try:
            await self.secret_store.delete(reference)
            # The local store deletes through the session; AWS has
            # nothing left to commit.
            await self.session.commit()
        except SecretStoreError:
            logger.warning(
                "Credentials row for %s/%s removed, but the secret could "
                "not be deleted; it will be reused if stored again",
                client_id,
                tool_code,
            )
        logger.info(
            "Deleted %s credentials for client %s", tool_code, client_id
        )

    async def _get_tool_or_raise(self, tool_code: str) -> IntegrationTool:
        tool = await self.tools.get_by_code(tool_code)
        if tool is None:
            raise IntegrationToolNotFoundError(tool_code)
        return tool

    @staticmethod
    def _to_response(
        credential: ClientToolCredential, tool_code: str
    ) -> ToolCredentialsResponse:
        return ToolCredentialsResponse(
            tool_code=tool_code,
            credential_names=credential.credential_names,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        )
