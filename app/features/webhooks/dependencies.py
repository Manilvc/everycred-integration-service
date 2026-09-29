"""Dependency providers for client webhooks."""

from typing import Annotated

from fastapi import Depends

from app.core.config import SettingsDep
from app.core.database import DbSession
from app.core.http_client import HttpClient
from app.core.secret_store import SecretStoreDep
from app.features.webhooks.repository import (
    ClientWebhookRepository,
    WebhookDeliveryRepository,
)
from app.features.webhooks.service import WebhookService


def get_webhook_service(
    session: DbSession,
    secret_store: SecretStoreDep,
    http_client: HttpClient,
    settings: SettingsDep,
) -> WebhookService:
    """Build the webhook service bound to the request's session."""
    return WebhookService(
        session=session,
        webhooks=ClientWebhookRepository(session),
        deliveries=WebhookDeliveryRepository(session),
        secret_store=secret_store,
        http_client=http_client,
        settings=settings,
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
