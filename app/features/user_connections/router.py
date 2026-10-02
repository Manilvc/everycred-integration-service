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

IntegrationTypeCode = Annotated[
    str, Path(min_length=1, max_length=CODE_MAX_LENGTH)
]

_TYPE_ERRORS = {
    status.HTTP_403_FORBIDDEN: {
        "model": ErrorResponse,
        "description": "Integration not enabled for this client",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Unknown integration type or no connection",
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
    "/{integration_type_code}",
    response_model=UserConnectionResponse,
    summary="Get a user's connection to one integration",
    responses=_TYPE_ERRORS,
)
async def get_user_connection(
    user_uuid: uuid.UUID,
    integration_type_code: IntegrationTypeCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
) -> UserConnectionResponse:
    """Return one connection, with parameter values hidden."""
    return await service.get_connection(
        current_client, user_uuid, integration_type_code
    )


@router.put(
    "/{integration_type_code}",
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
    integration_type_code: IntegrationTypeCode,
    parameters: ConnectionParameters,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
) -> UserConnectionResponse:
    """Store ``args`` and ``kwargs``, encrypted, and connect with them.

    Replaces any earlier parameters, then runs the connector right away.
    The response's `status` is `connected`, or `failed` with the reason
    in `last_error` (the parameters are still saved). It stays `pending`
    only when the type has no usable tool yet. The connect endpoint
    retries with the saved parameters.
    """
    return await service.save_parameters(
        current_client, user_uuid, integration_type_code, parameters
    )


@router.post(
    "/{integration_type_code}/connect",
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
    integration_type_code: IntegrationTypeCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
) -> UserConnectionResponse:
    """Run the connector as ``connect(*args, **kwargs)``.

    On failure the response is ``502`` and the connection is saved with
    status ``failed`` and a ``last_error`` explaining why.
    """
    return await service.connect(
        current_client, user_uuid, integration_type_code
    )


@router.post(
    "/{integration_type_code}/operations/{operation}",
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
    integration_type_code: IntegrationTypeCode,
    operation: OperationName,
    request: OperationRequest,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
) -> OperationResultResponse:
    """Call the tool the client uses for this type, for this user.

    The service loads the user's saved inputs and the client's tool
    credentials from the secret store, merges the `kwargs` sent here on
    top, runs the operation, and returns a normalised result. Find each
    tool's operations and required inputs in
    `GET /v1/client/integration-tools`.

    A provider rejecting the request (an invalid document number, say)
    is `200` with `success: false`; errors mean the call could not be
    made or completed.
    """
    return await service.run_operation(
        current_client, user_uuid, integration_type_code, operation, request
    )


@router.delete(
    "/{integration_type_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user's connection",
    responses=_TYPE_ERRORS,
)
async def delete_user_connection(
    user_uuid: uuid.UUID,
    integration_type_code: IntegrationTypeCode,
    current_client: CurrentClient,
    service: UserConnectionServiceDep,
) -> None:
    """Remove the connection and its stored parameters."""
    await service.delete_connection(
        current_client, user_uuid, integration_type_code
    )
