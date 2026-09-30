"""Configure client webhooks and queue events for delivery."""

import logging
import secrets
import uuid
from datetime import timedelta
from typing import Any

import httpx
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.clients.models import Client
from app.features.webhooks import delivery as webhook_delivery
from app.features.webhooks.models import (
    DELIVERY_ERROR_MAX_LENGTH,
    ClientWebhook,
    DeliveryStatus,
    WebhookDelivery,
)
from app.features.webhooks.repository import (
    ClientWebhookRepository,
    WebhookDeliveryRepository,
)
from app.features.webhooks.schemas import (
    WebhookResponse,
    WebhookTestResponse,
    WebhookUpdate,
)

logger = logging.getLogger(__name__)

SECRET_PREFIX = "whsec_"  # noqa: S105 - prefix, not a secret
# Waits between attempts; the last value repeats until max attempts.
RETRY_DELAYS = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=1),
)


class WebhookNotConfiguredError(NotFoundError):
    """Raised when the client has no webhook endpoint."""

    error_code = "webhook_not_configured"

    def __init__(self) -> None:
        super().__init__("No webhook endpoint is configured.")


class InvalidWebhookUrlError(AppError):
    """Raised when a webhook URL is not allowed."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "invalid_webhook_url"


def webhook_secret_name(client_id: uuid.UUID) -> str:
    """Secret store name for a client's webhook signing secret."""
    return f"clients/{client_id}/webhook"


def retry_delay(attempts: int) -> timedelta:
    """How long to wait after ``attempts`` failed attempts."""
    return RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]


class WebhookService:
    """Manages a client's endpoint and queues session events for it."""

    def __init__(
        self,
        session: AsyncSession,
        webhooks: ClientWebhookRepository,
        deliveries: WebhookDeliveryRepository,
        secret_store: SecretStore,
        http_client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.webhooks = webhooks
        self.deliveries = deliveries
        self.secret_store = secret_store
        self.http_client = http_client
        self.settings = settings

    async def get(self, client: Client) -> WebhookResponse:
        """Return the endpoint, without its secret.

        Raises:
            WebhookNotConfiguredError: None is configured.
        """
        webhook = await self._get_or_raise(client)
        return self._to_response(webhook)

    async def configure(
        self, client: Client, update: WebhookUpdate
    ) -> WebhookResponse:
        """Create or change the endpoint.

        A new endpoint gets a signing secret, returned once. Existing
        endpoints keep theirs unless ``rotate_secret`` is set.

        Raises:
            InvalidWebhookUrlError: The URL is not HTTPS or not allowed.
            SecretStoreError: The secret could not be stored.
        """
        url = update.url_text()
        try:
            webhook_delivery.check_url_shape(
                url, self.settings.webhook_allow_private_networks
            )
            await webhook_delivery.check_destination(
                url, self.settings.webhook_allow_private_networks
            )
        except webhook_delivery.UnsafeDestinationError as exc:
            raise InvalidWebhookUrlError(str(exc)) from exc

        webhook = await self.webhooks.get_for_client(client.id)
        new_secret: str | None = None
        if webhook is None or update.rotate_secret:
            new_secret = SECRET_PREFIX + secrets.token_urlsafe(32)
        if webhook is None:
            reference = await self.secret_store.create(
                webhook_secret_name(client.id),
                {"secret": new_secret},
                tags={"client_id": str(client.id), "purpose": "webhook"},
            )
            webhook = ClientWebhook(
                client_id=client.id, url=url, secret_reference=reference
            )
            self.webhooks.add(webhook)
        elif new_secret is not None:
            await self.secret_store.replace(
                webhook.secret_reference, {"secret": new_secret}
            )
        webhook.url = url
        webhook.is_active = update.is_active
        webhook.updated_at = utc_now()
        await self.session.commit()

        logger.info(
            "Client %s webhook set (active=%s, secret rotated=%s)",
            client.id,
            webhook.is_active,
            new_secret is not None,
        )
        response = self._to_response(webhook)
        response.signing_secret = new_secret
        return response

    async def delete(self, client: Client) -> None:
        """Remove the endpoint and schedule its secret for deletion.

        Raises:
            WebhookNotConfiguredError: None is configured.
        """
        webhook = await self._get_or_raise(client)
        reference = webhook.secret_reference
        await self.webhooks.delete(webhook)
        await self.session.commit()
        try:
            await self.secret_store.delete(reference)
            await self.session.commit()
        except SecretStoreError:
            logger.warning(
                "Webhook of client %s removed; its secret could not be "
                "deleted and will be reused if configured again",
                client.id,
            )

    async def send_test(self, client: Client) -> WebhookTestResponse:
        """Send a ``webhook.test`` event now and report what happened.

        Raises:
            WebhookNotConfiguredError: None is configured.
        """
        webhook = await self._get_or_raise(client)
        secret = await self._secret(webhook)
        delivery_id = uuid.uuid4().hex
        result = await webhook_delivery.send(
            self.http_client,
            url=webhook.url,
            secret=secret,
            event="webhook.test",
            delivery_id=delivery_id,
            payload={
                "id": delivery_id,
                "event": "webhook.test",
                "created_at": utc_now().isoformat(),
                "data": {"client_id": str(client.id)},
            },
            timeout_seconds=self.settings.webhook_timeout_seconds,
            allow_private_networks=(
                self.settings.webhook_allow_private_networks
            ),
        )
        return WebhookTestResponse(
            delivered=result.delivered,
            status_code=result.status_code,
            error=result.error,
        )

    async def enqueue(
        self,
        client_id: uuid.UUID,
        event: str,
        data: dict[str, Any],
        session_id: uuid.UUID | None = None,
    ) -> None:
        """Queue an event if the client has an active endpoint.

        Only staged; it is committed with the caller's unit of work, so
        an event is never sent for a change that was rolled back.
        """
        webhook = await self.webhooks.get_for_client(client_id)
        if webhook is None or not webhook.is_active:
            return
        delivery_id = uuid.uuid4()
        now = utc_now()
        self.deliveries.add(
            WebhookDelivery(
                id=delivery_id,
                client_id=client_id,
                session_id=session_id,
                event=event,
                payload={
                    "id": delivery_id.hex,
                    "event": event,
                    "created_at": now.isoformat(),
                    "data": data,
                },
                next_attempt_at=now,
            )
        )

    async def deliver_due(self, limit: int = 20) -> int:
        """Attempt every pending delivery that is due; return the count.

        Used by the worker. Each delivery is committed on its own, so one
        slow endpoint cannot hold back the rest's bookkeeping.
        """
        due = await self.deliveries.due(utc_now(), limit)
        for delivery in due:
            await self._attempt(delivery)
            await self.session.commit()
        return len(due)

    async def _attempt(self, delivery: WebhookDelivery) -> None:
        webhook = await self.webhooks.get_for_client(delivery.client_id)
        delivery.attempts += 1
        if webhook is None or not webhook.is_active:
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = "no active webhook endpoint"
            return
        try:
            secret = await self._secret(webhook)
        except SecretStoreError:
            self._schedule_retry(delivery, "signing secret unavailable")
            return

        result = await webhook_delivery.send(
            self.http_client,
            url=webhook.url,
            secret=secret,
            event=delivery.event,
            delivery_id=delivery.id.hex,
            payload=delivery.payload,
            timeout_seconds=self.settings.webhook_timeout_seconds,
            allow_private_networks=(
                self.settings.webhook_allow_private_networks
            ),
        )
        delivery.last_status_code = result.status_code
        if result.delivered:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = utc_now()
            delivery.last_error = None
            logger.info(
                "Delivered %s %s to client %s",
                delivery.event,
                delivery.id,
                delivery.client_id,
            )
        elif not result.retryable:
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = (result.error or "")[
                :DELIVERY_ERROR_MAX_LENGTH
            ]
            logger.warning(
                "Gave up on %s %s: %s",
                delivery.event,
                delivery.id,
                result.error,
            )
        else:
            self._schedule_retry(delivery, result.error or "failed")

    def _schedule_retry(self, delivery: WebhookDelivery, error: str) -> None:
        delivery.last_error = error[:DELIVERY_ERROR_MAX_LENGTH]
        if delivery.attempts >= self.settings.webhook_max_attempts:
            delivery.status = DeliveryStatus.FAILED
            logger.warning(
                "Gave up on %s %s after %s attempts: %s",
                delivery.event,
                delivery.id,
                delivery.attempts,
                error,
            )
            return
        delivery.next_attempt_at = utc_now() + retry_delay(delivery.attempts)

    async def _secret(self, webhook: ClientWebhook) -> str:
        stored = await self.secret_store.read(webhook.secret_reference)
        return str(stored["secret"])

    async def _get_or_raise(self, client: Client) -> ClientWebhook:
        webhook = await self.webhooks.get_for_client(client.id)
        if webhook is None:
            raise WebhookNotConfiguredError
        return webhook

    @staticmethod
    def _to_response(webhook: ClientWebhook) -> WebhookResponse:
        return WebhookResponse(
            url=webhook.url,
            is_active=webhook.is_active,
            created_at=webhook.created_at,
            updated_at=webhook.updated_at,
        )
