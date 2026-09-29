"""Database access for client tool connections."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.client_integrations.models import ClientToolConnection


class ClientToolConnectionRepository:
    """Queries for :class:`ClientToolConnection` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, client_id: uuid.UUID, integration_tool_id: uuid.UUID
    ) -> ClientToolConnection | None:
        """Return the client's connection to one tool, or None."""
        return await self.session.scalar(
            select(ClientToolConnection).where(
                ClientToolConnection.client_id == client_id,
                ClientToolConnection.integration_tool_id
                == integration_tool_id,
            )
        )

    async def by_tool_for_client(
        self, client_id: uuid.UUID
    ) -> dict[uuid.UUID, ClientToolConnection]:
        """Return all of a client's tool connections keyed by tool id."""
        connections = await self.session.scalars(
            select(ClientToolConnection).where(
                ClientToolConnection.client_id == client_id
            )
        )
        return {
            connection.integration_tool_id: connection
            for connection in connections
        }

    def add(self, connection: ClientToolConnection) -> None:
        """Stage a new connection for insertion."""
        self.session.add(connection)
