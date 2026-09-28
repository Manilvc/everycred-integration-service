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
from app.features.clients.exceptions import (
    ApiKeyNotFoundError,
    ClientCodeTakenError,
    ClientNotFoundError,
    InvalidApiKeyError,
)
from app.features.clients.models import (
    Client,
    ClientApiKey,
    ClientIntegrationConfig,
)
from app.features.clients.repository import (
    ClientApiKeyRepository,
    ClientIntegrationConfigRepository,
    ClientRepository,
)
from app.features.clients.schemas import (
    ApiKeyCreate,
    ClientConfigurationResponse,
    ClientCreate,
    ClientFilters,
    ClientResponse,
    ClientSummary,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
)
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
    return IntegrationConfigResponse(
        integration_type_code=integration_type.code,
        integration_type_name=integration_type.name,
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
    ) -> None:
        self.session = session
        self.clients = clients
        self.configs = configs
        self.integration_types = integration_types

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
        """
        await _get_client_or_raise(self.clients, client_id)
        integration_type = await self.integration_types.get_by_code(
            integration_type_code
        )
        if integration_type is None:
            raise IntegrationTypeNotFoundError(integration_type_code)

        config = await self.configs.get(client_id, integration_type.id)
        if config is None:
            config = ClientIntegrationConfig(
                client_id=client_id,
                integration_type_id=integration_type.id,
            )
            self.configs.add(config)
        config.is_enabled = update.is_enabled
        config.settings = update.settings
        await self.session.commit()

        logger.info(
            "Client %s configuration for %s updated (enabled=%s)",
            client_id,
            integration_type.code,
            update.is_enabled,
        )
        return _to_config_response(config, integration_type)

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
