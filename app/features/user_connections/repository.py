"""Database access for user integration connections."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.integration_types.models import IntegrationType
from app.features.user_connections.models import UserIntegrationConnection


class UserConnectionRepository:
    """Queries for :class:`UserIntegrationConnection` rows.

    Every query is scoped by ``client_id`` so one client can never read
    or change another client's users, even with the same user UUID.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self,
        client_id: uuid.UUID,
        user_uuid: uuid.UUID,
        integration_type_id: uuid.UUID,
    ) -> UserIntegrationConnection | None:
        """Return the user's connection for one integration type."""
        return await self.session.scalar(
            select(UserIntegrationConnection).where(
                UserIntegrationConnection.client_id == client_id,
                UserIntegrationConnection.user_uuid == user_uuid,
                UserIntegrationConnection.integration_type_id
                == integration_type_id,
            )
        )

    async def list_for_user(
        self, client_id: uuid.UUID, user_uuid: uuid.UUID
    ) -> Sequence[tuple[UserIntegrationConnection, IntegrationType]]:
        """Return all of a user's connections with their types."""
        rows = await self.session.execute(
            select(UserIntegrationConnection, IntegrationType)
            .join(
                IntegrationType,
                IntegrationType.id
                == UserIntegrationConnection.integration_type_id,
            )
            .where(
                UserIntegrationConnection.client_id == client_id,
                UserIntegrationConnection.user_uuid == user_uuid,
            )
            .order_by(IntegrationType.display_order, IntegrationType.name)
        )
        return rows.all()

    def add(self, connection: UserIntegrationConnection) -> None:
        """Stage a new connection for insertion."""
        self.session.add(connection)

    async def delete(self, connection: UserIntegrationConnection) -> None:
        """Stage a connection for deletion."""
        await self.session.delete(connection)
