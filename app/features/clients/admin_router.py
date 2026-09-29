"""Super admin routes for managing clients, API keys, and settings."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.features.clients.dependencies import (
    ApiKeyServiceDep,
    ClientConfigurationServiceDep,
    ClientCredentialServiceDep,
    ClientServiceDep,
)
from app.features.clients.schemas import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ClientCreate,
    ClientQuery,
    ClientResponse,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
    ToolCredentialsResponse,
    ToolCredentialsUpdate,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.features.super_admins.dependencies import (
    CurrentSuperAdmin,
    get_current_super_admin,
)
from app.shared.schemas import ErrorResponse, Page

router = APIRouter(
    prefix="/clients",
    tags=["Clients"],
    # Every route here is super-admin only; declaring it on the router
    # means a new route cannot forget the check.
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

_CLIENT_NOT_FOUND = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Client not found",
    }
}

IntegrationTypeCode = Annotated[
    str, Path(min_length=1, max_length=CODE_MAX_LENGTH)
]
ToolCode = Annotated[str, Path(min_length=1, max_length=TOOL_CODE_MAX_LENGTH)]
_CREDENTIAL_ERRORS = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Client, tool, or stored credentials not found",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ErrorResponse,
        "description": "The secret store could not be reached",
    },
}


@router.post(
    "",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a client project",
    responses={
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Client code already in use",
        }
    },
)
async def create_client(
    details: ClientCreate,
    current_super_admin: CurrentSuperAdmin,
    service: ClientServiceDep,
) -> ClientResponse:
    """Create a client that can later be issued API keys."""
    client = await service.create_client(
        details, created_by=current_super_admin
    )
    return ClientResponse.model_validate(client)


@router.get(
    "",
    response_model=Page[ClientResponse],
    summary="List client projects",
)
async def list_clients(
    filters: ClientQuery, service: ClientServiceDep
) -> Page[ClientResponse]:
    """Return clients ordered by name; inactive ones only on request."""
    return await service.list_clients(filters)


@router.get(
    "/{client_id}",
    response_model=ClientResponse,
    summary="Get a client project",
    responses=_CLIENT_NOT_FOUND,
)
async def get_client(
    client_id: uuid.UUID, service: ClientServiceDep
) -> ClientResponse:
    """Return a single client."""
    return ClientResponse.model_validate(await service.get_client(client_id))


@router.post(
    "/{client_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an API key for a client",
    responses=_CLIENT_NOT_FOUND,
)
async def create_api_key(
    client_id: uuid.UUID,
    options: ApiKeyCreate,
    response: Response,
    current_super_admin: CurrentSuperAdmin,
    service: ApiKeyServiceDep,
) -> ApiKeyCreatedResponse:
    """Issue a new key. The full key appears only in this response."""
    issued_key = await service.issue_api_key(
        client_id, options, issued_by=current_super_admin
    )
    # The body contains a credential; proxies and browsers must not
    # keep a copy.
    response.headers["Cache-Control"] = "no-store"
    key_details = ApiKeyResponse.model_validate(issued_key.record)
    return ApiKeyCreatedResponse(
        **key_details.model_dump(), api_key=issued_key.plaintext
    )


@router.get(
    "/{client_id}/api-keys",
    response_model=list[ApiKeyResponse],
    summary="List a client's API keys",
    responses=_CLIENT_NOT_FOUND,
)
async def list_api_keys(
    client_id: uuid.UUID, service: ApiKeyServiceDep
) -> list[ApiKeyResponse]:
    """Return key metadata, newest first. Keys themselves are not shown."""
    api_keys = await service.list_api_keys(client_id)
    return [ApiKeyResponse.model_validate(api_key) for api_key in api_keys]


@router.post(
    "/{client_id}/api-keys/{api_key_id}/revoke",
    response_model=ApiKeyResponse,
    summary="Revoke a client's API key",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Client or API key not found",
        }
    },
)
async def revoke_api_key(
    client_id: uuid.UUID,
    api_key_id: uuid.UUID,
    service: ApiKeyServiceDep,
) -> ApiKeyResponse:
    """Disable a key immediately. Calling this again has no effect."""
    api_key = await service.revoke_api_key(client_id, api_key_id)
    return ApiKeyResponse.model_validate(api_key)


@router.get(
    "/{client_id}/integrations",
    response_model=list[IntegrationConfigResponse],
    summary="List a client's integration settings",
    responses=_CLIENT_NOT_FOUND,
)
async def list_client_integrations(
    client_id: uuid.UUID, service: ClientConfigurationServiceDep
) -> list[IntegrationConfigResponse]:
    """Return every configuration of the client, enabled or not."""
    return await service.list_configs(client_id)


@router.put(
    "/{client_id}/integrations/{integration_type_code}",
    response_model=IntegrationConfigResponse,
    summary="Set a client's settings for one integration type",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Client or integration type not found",
        }
    },
)
async def set_client_integration(
    client_id: uuid.UUID,
    integration_type_code: IntegrationTypeCode,
    update: IntegrationConfigUpdate,
    service: ClientConfigurationServiceDep,
) -> IntegrationConfigResponse:
    """Create or replace the configuration; settings are not merged."""
    return await service.set_config(client_id, integration_type_code, update)


@router.put(
    "/{client_id}/tools/{tool_code}/credentials",
    response_model=ToolCredentialsResponse,
    summary="Store a client's credentials for a tool",
    responses=_CREDENTIAL_ERRORS,
)
async def set_tool_credentials(
    client_id: uuid.UUID,
    tool_code: ToolCode,
    update: ToolCredentialsUpdate,
    service: ClientCredentialServiceDep,
) -> ToolCredentialsResponse:
    """Save credentials (for example an API token) in the secret store.

    Only a reference is kept in the database. Values are never returned
    by any endpoint; calling this again replaces them (rotation).
    """
    return await service.set_credentials(client_id, tool_code, update)


@router.get(
    "/{client_id}/tools/{tool_code}/credentials",
    response_model=ToolCredentialsResponse,
    summary="See which credentials a client has stored for a tool",
    responses=_CREDENTIAL_ERRORS,
)
async def get_tool_credentials(
    client_id: uuid.UUID,
    tool_code: ToolCode,
    service: ClientCredentialServiceDep,
) -> ToolCredentialsResponse:
    """Return the stored credential names and dates, never the values."""
    return await service.get_credentials(client_id, tool_code)


@router.delete(
    "/{client_id}/tools/{tool_code}/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a client's credentials for a tool",
    responses=_CREDENTIAL_ERRORS,
)
async def delete_tool_credentials(
    client_id: uuid.UUID,
    tool_code: ToolCode,
    service: ClientCredentialServiceDep,
) -> None:
    """Remove the reference and schedule the secret for deletion."""
    await service.delete_credentials(client_id, tool_code)
