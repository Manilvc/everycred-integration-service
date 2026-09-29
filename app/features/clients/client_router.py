"""Routes that client projects call with their own API key."""

from typing import Annotated

from fastapi import APIRouter, Path, status

from app.features.clients.dependencies import (
    ClientConfigurationServiceDep,
    CurrentClient,
)
from app.features.clients.schemas import (
    ClientConfigurationResponse,
    ClientSettingsUpdate,
    IntegrationConfigResponse,
)
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.shared.schemas import ErrorResponse

router = APIRouter(
    prefix="/client",
    tags=["Client Self-Service"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, invalid, revoked, or expired API key",
        }
    },
)


@router.get(
    "/configuration",
    response_model=ClientConfigurationResponse,
    summary="Get the calling client's configuration",
)
async def get_own_configuration(
    current_client: CurrentClient,
    service: ClientConfigurationServiceDep,
) -> ClientConfigurationResponse:
    """Return the integrations enabled for the client owning the API key.

    Disabled configurations and inactive integration types are left out.
    """
    return await service.get_client_configuration(current_client)


@router.put(
    "/integrations/{integration_type_code}/settings",
    response_model=IntegrationConfigResponse,
    summary="Update the calling client's settings for one integration",
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Integration not enabled for this client",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Unknown integration type",
        },
    },
)
async def update_own_integration_settings(
    integration_type_code: Annotated[
        str, Path(min_length=1, max_length=CODE_MAX_LENGTH)
    ],
    update: ClientSettingsUpdate,
    current_client: CurrentClient,
    service: ClientConfigurationServiceDep,
) -> IntegrationConfigResponse:
    """Replace your settings for an integration a super admin enabled.

    Settings are replaced, not merged. Enabling the integration and
    choosing its tool remain super admin actions.
    """
    return await service.update_own_settings(
        current_client, integration_type_code, update
    )
