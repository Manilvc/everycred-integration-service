"""Routes for browsing integration tools.

Two routers share the same service: one for super admins, who see the
whole catalogue, and one for clients, who see only the tools usable for
their enabled integration types.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.integration_tools.dependencies import (
    IntegrationToolServiceDep,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.features.integration_tools.schemas import (
    ClientToolQuery,
    IntegrationToolQuery,
    IntegrationToolResponse,
    IntegrationToolUpsert,
)
from app.features.super_admins.dependencies import get_current_super_admin
from app.shared.schemas import ErrorResponse, Page

admin_router = APIRouter(
    prefix="/integration-tools",
    tags=["Integration Tools"],
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
    prefix="/client/integration-tools",
    tags=["Client Self-Service"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing, invalid, revoked, or expired API key",
        }
    },
)


@admin_router.get(
    "",
    response_model=Page[IntegrationToolResponse],
    summary="List integration tools",
)
async def list_integration_tools(
    filters: IntegrationToolQuery, service: IntegrationToolServiceDep
) -> Page[IntegrationToolResponse]:
    """Return the tool catalogue, ordered for display.

    Filter by `integration_type` code; inactive tools only with
    `include_inactive=true`. Each tool reports whether its connector is
    installed and which `args`/`kwargs` it accepts.
    """
    return await service.list_tools(filters)


@admin_router.put(
    "/{tool_code}",
    response_model=IntegrationToolResponse,
    summary="Create or replace an integration tool",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid definition, connector config, or type",
        }
    },
)
async def upsert_integration_tool(
    tool_code: Annotated[
        str,
        Path(
            min_length=2,
            max_length=TOOL_CODE_MAX_LENGTH,
            pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$",
        ),
    ],
    definition: IntegrationToolUpsert,
    service: IntegrationToolServiceDep,
) -> IntegrationToolResponse:
    """Define a tool, including a configuration-driven connector.

    The whole definition is replaced on every call. `connector_config`
    is validated before saving: HTTPS base URLs, relative operation
    paths, known placeholders, and credentials only through
    `{credentials.<name>}`. Set the credentials themselves per client
    with `PUT /v1/clients/{client_id}/tools/{tool_code}/credentials`.
    """
    return await service.upsert_tool(tool_code, definition)


@client_router.get(
    "",
    response_model=Page[IntegrationToolResponse],
    summary="List tools available to the calling client",
)
async def list_client_integration_tools(
    filters: ClientToolQuery,
    current_client: CurrentClient,
    service: IntegrationToolServiceDep,
) -> Page[IntegrationToolResponse]:
    """Return active tools for the integration types enabled for you.

    Use each tool's `connector.parameters` to know which `args` and
    `kwargs` to save for your users' connections.
    """
    return await service.list_tools_for_client(current_client, filters)
