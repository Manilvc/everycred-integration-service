"""Routes that client projects call with their own API key."""

from fastapi import APIRouter, status

from app.features.clients.dependencies import (
    ClientConfigurationServiceDep,
    CurrentClient,
)
from app.features.clients.schemas import ClientConfigurationResponse
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
