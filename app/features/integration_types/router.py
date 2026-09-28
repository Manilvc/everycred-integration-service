"""HTTP routes for browsing integration types."""

from fastapi import APIRouter, Depends, status

from app.features.integration_types.dependencies import (
    IntegrationTypeServiceDep,
)
from app.features.integration_types.schemas import (
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

    Only active types are returned unless ``include_inactive=true``.
    """
    return await service.list_integration_types(filters)
