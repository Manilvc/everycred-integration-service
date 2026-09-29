"""Routes a client uses to verify users and gather their data.

EveryCRED (or any client) starts a session when a holder accepts an
invite or a credential needs data, relays what the holder types (an
OTP, say) to the inputs endpoint, and learns the outcome from a webhook
or by polling. The client is always taken from the API key.
"""

import uuid

from fastapi import APIRouter, Depends, status

from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.sessions.dependencies import SessionServiceDep
from app.features.sessions.schemas import (
    SessionCreate,
    SessionInputs,
    SessionQuery,
    SessionResponse,
    SessionResultResponse,
)
from app.shared.schemas import ErrorResponse, Page

router = APIRouter(
    prefix="/client",
    tags=["Sessions"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)
_NOT_FOUND = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "No such session for this client",
    }
}
_CONFLICT = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "The session is not in a state that allows this",
    }
}


@router.post(
    "/users/{user_uuid}/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a verification or gather session",
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Integration type not enabled for this client",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Unknown integration type or flow",
        },
        **_CONFLICT,
    },
)
async def start_session(
    user_uuid: uuid.UUID,
    request: SessionCreate,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Start a flow for one user with the tool chosen for the type.

    The session runs straight away. It comes back ``awaiting_input``
    when a step needs something the holder must type (listed in
    ``awaiting_inputs``), ``completed`` with an ``outcome``, or
    ``failed`` with a ``failure_code``.
    """
    return await service.start(current_client, user_uuid, request)


@router.get(
    "/users/{user_uuid}/sessions",
    response_model=Page[SessionResponse],
    summary="List a user's sessions",
)
async def list_sessions(
    user_uuid: uuid.UUID,
    filters: SessionQuery,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> Page[SessionResponse]:
    """Return the user's sessions, newest first."""
    return await service.list_for_user(current_client, user_uuid, filters)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get a session's status",
    responses=_NOT_FOUND,
)
async def get_session(
    session_id: uuid.UUID,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Poll a session. Contains no inputs or attributes."""
    return await service.get(current_client, session_id)


@router.post(
    "/sessions/{session_id}/inputs",
    response_model=SessionResponse,
    summary="Submit the inputs a session is waiting for",
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def submit_session_inputs(
    session_id: uuid.UUID,
    request: SessionInputs,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Send what the holder entered, such as an OTP, and continue."""
    return await service.submit_inputs(current_client, session_id, request)


@router.get(
    "/sessions/{session_id}/result",
    response_model=SessionResultResponse,
    summary="Get a completed session's attributes",
    responses={
        **_NOT_FOUND,
        **_CONFLICT,
        status.HTTP_410_GONE: {
            "model": ErrorResponse,
            "description": "The result was deleted after retention",
        },
    },
)
async def get_session_result(
    session_id: uuid.UUID,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResultResponse:
    """Return the verified or gathered attributes.

    Available until ``data_expires_at``, after which they are deleted.
    """
    return await service.result(current_client, session_id)


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=SessionResponse,
    summary="Cancel an unfinished session",
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def cancel_session(
    session_id: uuid.UUID,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Stop the session and discard the inputs collected so far."""
    return await service.cancel(current_client, session_id)
