"""Routes behind a client admin's Integrations screen.

Called by the client's own backend with its ``X-API-Key``: the list of
integration types with their tool cards, a tool's drawer, Save, and
Test connection. A client only ever sees tools serving the types a
super admin enabled for it.

``user_router`` lists the same integrations for one of the client's
users, with that user's connection status.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.features.client_integrations.dependencies import (
    ClientIntegrationServiceDep,
)
from app.features.client_integrations.schemas import (
    ConnectionTestResponse,
    IntegrationsListingResponse,
    ToolConnectionDetail,
    ToolConnectionUpdate,
    UserIntegrationsListingResponse,
    UserIntegrationStatus,
)
from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.shared.schemas import ErrorResponse

router = APIRouter(
    prefix="/client/integrations",
    tags=["Client Integrations"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)

user_router = APIRouter(
    prefix="/client/users/{user_uuid}/integrations",
    tags=["User Connections"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)

ToolCode = Annotated[str, Path(min_length=1, max_length=TOOL_CODE_MAX_LENGTH)]
_TOOL_ERRORS = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Tool not available to this client",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ErrorResponse,
        "description": "The secret store could not be reached",
    },
}


@router.get(
    "",
    response_model=IntegrationsListingResponse,
    summary="List integrations grouped by type",
)
async def list_client_integrations(
    current_client: CurrentClient, service: ClientIntegrationServiceDep
) -> IntegrationsListingResponse:
    """Return every active integration type with the client's systems.

    The shape matches the EveryCRED Integrations screen: `groups` with
    `id`, `key`, `label`, `description`, and `systems`. A type the client
    has not enabled is listed with built-in systems only, such as the
    Holder Wallet App under Declare.

    System `status` is one of `available`, `needs_credentials`,
    `not_tested`, `configured`, `connected`, `failed`, `disabled`, or
    `unavailable` (no connector installed yet). Use a system's `code`
    for the drawer, Save, and Test connection endpoints.
    """
    return await service.list_integrations(current_client)


@router.get(
    "/{tool_code}",
    response_model=ToolConnectionDetail,
    summary="Get one tool's connection details",
    responses=_TOOL_ERRORS,
)
async def get_client_integration(
    tool_code: ToolCode,
    current_client: CurrentClient,
    service: ClientIntegrationServiceDep,
) -> ToolConnectionDetail:
    """Return what the tool drawer shows.

    Includes the auth method, which credentials the tool needs and which
    are stored (names only), the on/off switch, and the last test result.
    """
    return await service.get_tool(current_client, tool_code)


@router.put(
    "/{tool_code}",
    response_model=ToolConnectionDetail,
    summary="Save a tool's switch and credentials",
    responses=_TOOL_ERRORS,
)
async def update_client_integration(
    tool_code: ToolCode,
    update: ToolConnectionUpdate,
    current_client: CurrentClient,
    service: ClientIntegrationServiceDep,
) -> ToolConnectionDetail:
    """Turn the tool on or off and set, rotate, or remove credentials.

    Credentials are merged into what is stored, so send only the ones
    being changed. They go to the secret store and are never returned.
    Changing credentials resets the status until the next test.
    """
    return await service.update_tool(current_client, tool_code, update)


@router.post(
    "/{tool_code}/test",
    response_model=ConnectionTestResponse,
    summary="Test a tool's connection",
    responses=_TOOL_ERRORS,
)
async def test_client_integration(
    tool_code: ToolCode,
    current_client: CurrentClient,
    service: ClientIntegrationServiceDep,
) -> ConnectionTestResponse:
    """Check the stored credentials with the provider, without any user.

    Runs the tool's `test_operation`, or obtains an OAuth token, or at
    least confirms every credential is stored (`called_provider: false`).
    A failed test returns `200` with `success: false` and the reason.
    """
    return await service.test_tool(current_client, tool_code)


@user_router.get(
    "",
    response_model=UserIntegrationsListingResponse,
    summary="List the integrations a user is connected to",
)
async def list_user_integrations(
    user_uuid: uuid.UUID,
    current_client: CurrentClient,
    service: ClientIntegrationServiceDep,
    status_filter: Annotated[
        list[UserIntegrationStatus] | None,
        Query(
            alias="status",
            description=(
                "Statuses to return; repeat for several. Defaults to "
                "`connected`. Pass e.g. `status=not_connected&status="
                "pending` to see tools the user has not connected yet."
            ),
        ),
    ] = None,
) -> UserIntegrationsListingResponse:
    """Return the tools the user is connected to, grouped by type.

    By default only `connected` systems are returned: tools the user has
    a successful connection to (`PUT` then `POST .../connect` under
    `/client/users/{user_uuid}/connections`). Built-in tools such as the
    Holder Wallet App follow the same rule. Every group is listed, with
    an empty `systems` list where the user has nothing connected.

    Ask for other statuses with `status` (repeatable): `pending`,
    `failed`, `not_connected`, or `unavailable` (the client has the tool
    switched off or not set up). `tool_status` is the tool's status on
    the client's own screen.
    """
    return await service.list_user_integrations(
        current_client,
        user_uuid,
        statuses=status_filter or [UserIntegrationStatus.CONNECTED],
    )
