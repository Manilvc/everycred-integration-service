"""Dependency providers for verification and gather sessions."""

from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.dependencies import ConnectorRegistryDep
from app.connectors.registry import ConnectorRegistry
from app.core.config import Settings, SettingsDep
from app.core.database import DbSession
from app.core.http_client import HttpClient
from app.core.secret_store import SecretStore, SecretStoreDep
from app.features.client_integrations.repository import (
    ClientToolConnectionRepository,
)
from app.features.clients.repository import (
    ClientIntegrationConfigRepository,
    ClientToolCredentialRepository,
)
from app.features.integration_types.repository import (
    IntegrationTypeRepository,
)
from app.features.sessions.repository import SessionRepository
from app.features.sessions.service import SessionService
from app.features.user_connections.targets import IntegrationTargetResolver
from app.features.webhooks.repository import (
    ClientWebhookRepository,
    WebhookDeliveryRepository,
)
from app.features.webhooks.service import WebhookService


def build_webhook_service(
    session: AsyncSession,
    secret_store: SecretStore,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> WebhookService:
    """Assemble a webhook service; shared by the API and the worker."""
    return WebhookService(
        session=session,
        webhooks=ClientWebhookRepository(session),
        deliveries=WebhookDeliveryRepository(session),
        secret_store=secret_store,
        http_client=http_client,
        settings=settings,
    )


def build_session_service(
    session: AsyncSession,
    registry: ConnectorRegistry,
    secret_store: SecretStore,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> SessionService:
    """Assemble a session service; shared by the API and the worker."""
    tool_connections = ClientToolConnectionRepository(session)
    return SessionService(
        session=session,
        sessions=SessionRepository(session),
        targets=IntegrationTargetResolver(
            integration_types=IntegrationTypeRepository(session),
            client_configs=ClientIntegrationConfigRepository(session),
            tool_connections=tool_connections,
            credentials=ClientToolCredentialRepository(session),
            secret_store=secret_store,
            registry=registry,
            http_client=http_client,
        ),
        tool_connections=tool_connections,
        secret_store=secret_store,
        webhooks=build_webhook_service(
            session, secret_store, http_client, settings
        ),
        settings=settings,
    )


def get_session_service(
    session: DbSession,
    registry: ConnectorRegistryDep,
    secret_store: SecretStoreDep,
    http_client: HttpClient,
    settings: SettingsDep,
) -> SessionService:
    """Build the session service bound to the request's session."""
    return build_session_service(
        session, registry, secret_store, http_client, settings
    )


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
