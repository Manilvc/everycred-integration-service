"""Routes a client uses to connect its users to integrations.

Every route authenticates the client by its ``X-API-Key``. The client
is taken from the key, never from the URL, so a client can only reach
its own users.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import StringConstraints

from app.connectors.http.config import OPERATION_NAME_PATTERN
from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.features.user_connections.dependencies import (
    UserConnectionServiceDep,
)
from app.features.user_connections.schemas import (
    ConnectionParameters,
    OperationRequest,
    OperationResultResponse,
    UserConnectionResponse,
)
from app.shared.schemas import ErrorResponse

MAX_TYPE_FILTERS = 20

OperationName = Annotated[
    str, Path(min_length=1, max_length=64, pattern=OPERATION_NAME_PATTERN)
]

router = APIRouter(
    prefix="/client/users/{user_uuid}/connections",
    tags=["User Connections"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)

ConnectionCode = Annotated[
    str,
    Path(
        min_length=1,
        max_length=max(TOOL_CODE_MAX_LENGTH, 36),
        description=(
            "The tool to connect through: its code (`surepass`, "
            "`entra-id`) or id. An integration type code (`confirm`) "
            "also works, with `tool_code` or the type's default tool."
        ),
    ),
]
IntegrationTypeQuery = Annotated[
    str | None,
    Query(
        alias="integration_type",
        min_length=1,
        max_length=CODE_MAX_LENGTH,
        description=(
            "Only for a tool serving several of your integration types: "
            "which one the connection is for."
        ),
    ),
]
ToolCode = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=TOOL_CODE_MAX_LENGTH,
        description=(
            "Tool of the type, when it has several (e.g. `surepass`). "
            "Each tool has its own connection. The type's default tool "
            "when omitted."
        ),
    ),
]

_TYPE_ERRORS = {
    status.HTTP_403_FORBIDDEN: {
        "model": ErrorResponse,
        "description": "Integration not enabled for this client",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Unknown tool or integration type, or no connection",
    },
}


@router.get(
    "",
    response_model=list[UserConnectionResponse],
    summary="List a user's integration connections",
)
async def list_user_connections(
    user_uuid: uuid.UUID,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    integration_types: Annotated[
        list[Annotated[str, StringConstraints(max_length=CODE_MAX_LENGTH)]]
        | None,
        Query(
            alias="integration_type",
            max_length=MAX_TYPE_FILTERS,
            description=(
                "Integration type codes to return, e.g. `confirm`; repeat "
                "for several. Without it, every type is returned."
            ),
        ),
    ] = None,
) -> list[UserConnectionResponse]:
    """Return the user's connections, with parameter values hidden.

    `integration_type` (repeatable) keeps only those types, e.g.
    `?integration_type=confirm`; without it every connection is returned.
    """
    return await service.list_connections(
        current_client, user_uuid, integration_type_codes=integration_types
    )


@router.get(
    "/{code}",
    response_model=UserConnectionResponse,
    summary="Get a user's connection to one integration",
    responses=_TYPE_ERRORS,
)
async def get_user_connection(
    user_uuid: uuid.UUID,
    code: ConnectionCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    tool_code: ToolCode = None,
    integration_type: IntegrationTypeQuery = None,
) -> UserConnectionResponse:
    """Return the connection to one tool, with parameter values hidden."""
    integration_type_code, tool_code = await service.interpret_path(
        current_client, code, tool_code, integration_type
    )
    return await service.get_connection(
        current_client, user_uuid, integration_type_code, tool_code
    )


@router.put(
    "/{code}",
    response_model=UserConnectionResponse,
    summary="Save a user's connection parameters and connect",
    responses={
        **_TYPE_ERRORS,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Parameters invalid or do not fit the connector",
        },
    },
)
async def save_user_connection(
    user_uuid: uuid.UUID,
    code: ConnectionCode,
    parameters: ConnectionParameters,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    tool_code: ToolCode = None,
    integration_type: IntegrationTypeQuery = None,
) -> UserConnectionResponse:
    """Store ``args`` and ``kwargs``, encrypted, and connect with them.

    Replaces any earlier parameters, then runs the connector right away.
    The response's `status` is `connected`, or `failed` with the reason
    in `last_error` (the parameters are still saved). It stays `pending`
    only when the type has no usable tool yet. The connect endpoint
    retries with the saved parameters.

    Each tool has its own connection: `?tool_code=surepass` and
    `?tool_code=entra-id-signin` are kept side by side.
    """
    integration_type_code, tool_code = await service.interpret_path(
        current_client, code, tool_code, integration_type
    )
    return await service.save_parameters(
        current_client,
        user_uuid,
        integration_type_code,
        parameters,
        tool_code,
    )


@router.post(
    "/{code}/connect",
    response_model=UserConnectionResponse,
    summary="Connect again with the saved parameters",
    responses={
        **_TYPE_ERRORS,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Saved parameters do not fit the connector",
        },
        status.HTTP_501_NOT_IMPLEMENTED: {
            "model": ErrorResponse,
            "description": "No connector exists for this integration yet",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "The integration refused, failed, or timed out",
        },
    },
)
async def connect_user(
    user_uuid: uuid.UUID,
    code: ConnectionCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    tool_code: ToolCode = None,
    integration_type: IntegrationTypeQuery = None,
) -> UserConnectionResponse:
    """Run the connector as ``connect(*args, **kwargs)``.

    On failure the response is ``502`` and the connection is saved with
    status ``failed`` and a ``last_error`` explaining why.
    """
    integration_type_code, tool_code = await service.interpret_path(
        current_client, code, tool_code, integration_type
    )
    return await service.connect(
        current_client, user_uuid, integration_type_code, tool_code
    )


@router.post(
    "/{code}/operations/{operation}",
    response_model=OperationResultResponse,
    summary="Run an operation of the tool for the user",
    responses={
        **_TYPE_ERRORS,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": (
                "No tool chosen, tool inactive, or credentials missing"
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid or missing inputs",
        },
        status.HTTP_501_NOT_IMPLEMENTED: {
            "model": ErrorResponse,
            "description": "The tool has no connector yet",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "The provider failed, timed out, or answered badly",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The secret store could not be reached",
        },
    },
)
async def run_user_operation(
    user_uuid: uuid.UUID,
    code: ConnectionCode,
    operation: OperationName,
    request: OperationRequest,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    tool_code: ToolCode = None,
    integration_type: IntegrationTypeQuery = None,
) -> OperationResultResponse:
    """Call a tool of this type for this user.

    The service loads the user's saved inputs and the client's tool
    credentials from the secret store, merges the `kwargs` sent here on
    top, runs the operation, and returns a normalised result. Find each
    tool's operations and required inputs in
    `GET /v1/client/integration-tools`.

    A provider rejecting the request (an invalid document number, say)
    is `200` with `success: false`; errors mean the call could not be
    made or completed.
    """
    integration_type_code, tool_code = await service.interpret_path(
        current_client, code, tool_code, integration_type
    )
    return await service.run_operation(
        current_client,
        user_uuid,
        integration_type_code,
        operation,
        request,
        tool_code,
    )


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user's connection",
    responses=_TYPE_ERRORS,
)
async def delete_user_connection(
    user_uuid: uuid.UUID,
    code: ConnectionCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
    tool_code: ToolCode = None,
    integration_type: IntegrationTypeQuery = None,
) -> None:
    """Remove one tool's connection and its stored parameters."""
    integration_type_code, tool_code = await service.interpret_path(
        current_client, code, tool_code, integration_type
    )
    await service.delete_connection(
        current_client, user_uuid, integration_type_code, tool_code
    )
