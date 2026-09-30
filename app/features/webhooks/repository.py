"""Database access for client webhooks and deliveries."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.webhooks.models import (
    ClientWebhook,
    DeliveryStatus,
    WebhookDelivery,
)


class ClientWebhookRepository:
    """Queries for :class:`ClientWebhook` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_client(
        self, client_id: uuid.UUID
    ) -> ClientWebhook | None:
        """Return the client's webhook endpoint, or None."""
        return await self.session.scalar(
            select(ClientWebhook).where(ClientWebhook.client_id == client_id)
        )

    def add(self, webhook: ClientWebhook) -> None:
        """Stage a new endpoint."""
        self.session.add(webhook)

    async def delete(self, webhook: ClientWebhook) -> None:
        """Stage an endpoint for deletion."""
        await self.session.delete(webhook)


class WebhookDeliveryRepository:
    """Queries for :class:`WebhookDelivery` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, delivery: WebhookDelivery) -> None:
        """Stage a new delivery."""
        self.session.add(delivery)

    async def due(
        self, now: datetime, limit: int
    ) -> Sequence[WebhookDelivery]:
        """Return pending deliveries whose next attempt time has come."""
        deliveries = await self.session.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(limit)
        )
        return deliveries.all()
