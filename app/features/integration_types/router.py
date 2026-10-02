"""HTTP routes for browsing integration types.

``router`` is for super admins; ``client_router`` gives the same
catalogue to a client's backend with its ``X-API-Key``.
"""

from fastapi import APIRouter, Depends, status

from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.integration_types.dependencies import (
    IntegrationTypeServiceDep,
)
from app.features.integration_types.schemas import (
    ClientIntegrationTypeQuery,
    ClientIntegrationTypeResponse,
    IntegrationTypeQuery,
    IntegrationTypeResponse,
)
from app.features.super_admins.dependencies import get_current_super_admin
from app.shared.schemas import ErrorResponse, Page

router = APIRouter(
    prefix="/integration-types",
    tags=["Integration Types"],
    # Declared on the router so every route added here later is
    # protected too, not only the ones that remember to ask for it.
    dependencies=[Depends(get_current_super_admin)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid super admin token",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Token does not belong to a super admin",
        },
    },
)

client_router = APIRouter(
    prefix="/client/integration-types",
    tags=["Client Self-Service"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, invalid, revoked, or expired API key",
        }
    },
)


@router.get(
    "",
    response_model=Page[IntegrationTypeResponse],
    summary="List integration types",
)
async def list_integration_types(
    filters: IntegrationTypeQuery,
    service: IntegrationTypeServiceDep,
) -> Page[IntegrationTypeResponse]:
    """Return integration types, ordered for display.

    `direction` filters by `inbound` (Confirm, Gather, Declare) or
    `outbound` (Enforcement, Records); without it, or with `all`, both
    are returned. Only active types are returned unless
    ``include_inactive=true``.
    """
    return await service.list_integration_types(filters)


@client_router.get(
    "",
    response_model=Page[ClientIntegrationTypeResponse],
    summary="List integration types for the calling client",
)
async def list_client_integration_types(
    filters: ClientIntegrationTypeQuery,
    current_client: CurrentClient,
    service: IntegrationTypeServiceDep,
) -> Page[ClientIntegrationTypeResponse]:
    """Return the active integration types, ordered for display.

    `direction` filters by `inbound` (Confirm, Gather, Declare) or
    `outbound` (Enforcement, Records); without it, or with `all`, both
    are returned. `is_enabled` tells which types are switched on for
    you.
    """
    return await service.list_for_client(current_client, filters)
