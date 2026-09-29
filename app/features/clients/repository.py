"""Database access for clients, their API keys, and their settings."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.clients.models import (
    Client,
    ClientApiKey,
    ClientIntegrationConfig,
    ClientToolCredential,
)
from app.features.integration_types.models import IntegrationType


class ClientRepository:
    """Queries for :class:`Client` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, client_id: uuid.UUID) -> Client | None:
        """Return the client with this id, or None."""
        return await self.session.get(Client, client_id)

    async def get_by_code(self, code: str) -> Client | None:
        """Return the client with this code, or None."""
        return await self.session.scalar(
            select(Client).where(Client.code == code)
        )

    async def list_page(
        self, *, include_inactive: bool, limit: int, offset: int
    ) -> tuple[Sequence[Client], int]:
        """Return one page of clients ordered by name, and the total."""
        filters = [] if include_inactive else [Client.is_active]
        total = await self.session.scalar(
            select(func.count()).select_from(Client).where(*filters)
        )
        clients = await self.session.scalars(
            select(Client)
            .where(*filters)
            .order_by(Client.name, Client.id)
            .limit(limit)
            .offset(offset)
        )
        return clients.all(), total or 0

    def add(self, client: Client) -> None:
        """Stage a new client for insertion."""
        self.session.add(client)


class ClientApiKeyRepository:
    """Queries for :class:`ClientApiKey` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_prefix(self, key_prefix: str) -> ClientApiKey | None:
        """Return the key with this public prefix, or None."""
        return await self.session.scalar(
            select(ClientApiKey).where(ClientApiKey.key_prefix == key_prefix)
        )

    async def get_for_client(
        self, api_key_id: uuid.UUID, client_id: uuid.UUID
    ) -> ClientApiKey | None:
        """Return the key only if it belongs to ``client_id``."""
        return await self.session.scalar(
            select(ClientApiKey).where(
                ClientApiKey.id == api_key_id,
                ClientApiKey.client_id == client_id,
            )
        )

    async def list_for_client(
        self, client_id: uuid.UUID
    ) -> Sequence[ClientApiKey]:
        """Return every key of a client, newest first."""
        api_keys = await self.session.scalars(
            select(ClientApiKey)
            .where(ClientApiKey.client_id == client_id)
            .order_by(ClientApiKey.created_at.desc(), ClientApiKey.id)
        )
        return api_keys.all()

    def add(self, api_key: ClientApiKey) -> None:
        """Stage a new key for insertion."""
        self.session.add(api_key)


class ClientIntegrationConfigRepository:
    """Queries for :class:`ClientIntegrationConfig` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, client_id: uuid.UUID, integration_type_id: uuid.UUID
    ) -> ClientIntegrationConfig | None:
        """Return the client's configuration for one integration type."""
        return await self.session.scalar(
            select(ClientIntegrationConfig).where(
                ClientIntegrationConfig.client_id == client_id,
                ClientIntegrationConfig.integration_type_id
                == integration_type_id,
            )
        )

    async def list_with_types(
        self, client_id: uuid.UUID, *, usable_only: bool
    ) -> Sequence[tuple[ClientIntegrationConfig, IntegrationType]]:
        """Return a client's configurations paired with their types.

        Args:
            client_id: Client whose configurations to return.
            usable_only: Keep only enabled configurations of active
                integration types, which is what the client itself sees.
        """
        statement = (
            select(ClientIntegrationConfig, IntegrationType)
            .join(
                IntegrationType,
                IntegrationType.id
                == ClientIntegrationConfig.integration_type_id,
            )
            .where(ClientIntegrationConfig.client_id == client_id)
            .order_by(IntegrationType.display_order, IntegrationType.name)
        )
        if usable_only:
            statement = statement.where(
                ClientIntegrationConfig.is_enabled, IntegrationType.is_active
            )
        rows = await self.session.execute(statement)
        return rows.all()

    def add(self, config: ClientIntegrationConfig) -> None:
        """Stage a new configuration for insertion."""
        self.session.add(config)


class ClientToolCredentialRepository:
    """Queries for :class:`ClientToolCredential` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, client_id: uuid.UUID, integration_tool_id: uuid.UUID
    ) -> ClientToolCredential | None:
        """Return the client's credential reference for one tool."""
        return await self.session.scalar(
            select(ClientToolCredential).where(
                ClientToolCredential.client_id == client_id,
                ClientToolCredential.integration_tool_id
                == integration_tool_id,
            )
        )

    async def by_tool_for_client(
        self, client_id: uuid.UUID
    ) -> dict[uuid.UUID, ClientToolCredential]:
        """Return all of a client's credential references by tool id."""
        credentials = await self.session.scalars(
            select(ClientToolCredential).where(
                ClientToolCredential.client_id == client_id
            )
        )
        return {
            credential.integration_tool_id: credential
            for credential in credentials
        }

    def add(self, credential: ClientToolCredential) -> None:
        """Stage a new credential reference for insertion."""
        self.session.add(credential)

    async def delete(self, credential: ClientToolCredential) -> None:
        """Stage a credential reference for deletion."""
        await self.session.delete(credential)
