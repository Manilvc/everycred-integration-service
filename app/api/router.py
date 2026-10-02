"""Versioned API router that collects every feature's routes.

Each feature exposes its own ``APIRouter``; this module is the single
place where they are mounted under ``/v1``. To add a feature::

    from app.features.verifications.router import (
        router as verifications_router,
    )

    api_v1_router.include_router(verifications_router)
"""

from fastapi import APIRouter, status

from app.features.client_integrations.router import (
    router as client_integrations_router,
)
from app.features.client_integrations.router import (
    user_router as user_integrations_router,
)
from app.features.clients.admin_router import router as clients_router
from app.features.clients.client_router import (
    router as client_self_service_router,
)
from app.features.integration_tools.router import (
    admin_router as integration_tools_router,
)
from app.features.integration_tools.router import (
    client_router as client_integration_tools_router,
)
from app.features.integration_types.router import (
    router as integration_types_router,
)
from app.features.sessions.router import router as sessions_router
from app.features.super_admins.router import router as super_admins_router
from app.features.user_connections.router import (
    router as user_connections_router,
)
from app.features.webhooks.router import router as webhooks_router
from app.shared.schemas import ErrorResponse

api_v1_router = APIRouter(
    prefix="/v1",
    # Documented once here so every versioned route shows the shared
    # error envelope in OpenAPI.
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)

api_v1_router.include_router(super_admins_router)
api_v1_router.include_router(integration_types_router)
api_v1_router.include_router(integration_tools_router)
api_v1_router.include_router(clients_router)
api_v1_router.include_router(client_self_service_router)
api_v1_router.include_router(client_integration_tools_router)
api_v1_router.include_router(client_integrations_router)
api_v1_router.include_router(user_integrations_router)
api_v1_router.include_router(user_connections_router)
api_v1_router.include_router(sessions_router)
api_v1_router.include_router(webhooks_router)
