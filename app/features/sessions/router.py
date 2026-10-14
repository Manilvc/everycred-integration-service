"""Routes a client uses to verify users and gather their data.

EveryCRED (or any client) starts a session when a holder accepts an
invite or a credential needs data, relays what the holder types (an
OTP, say) to the inputs endpoint, and learns the outcome from a webhook
or by polling. The client is always taken from the API key.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.features.sessions.dependencies import SessionServiceDep
from app.features.sessions.schemas import (
    FlowCatalogResponse,
    SessionAuthorizationCallback,
    SessionCreate,
    SessionInputs,
    SessionQuery,
    SessionResponse,
    SessionResultResponse,
    VerifiedUserQuery,
    VerifiedUserResponse,
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


@router.get(
    "/integration-types/{integration_type}/flows",
    response_model=FlowCatalogResponse,
    summary="Describe the flows a holder screen can offer",
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Integration type not enabled for this client",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Unknown integration type",
        },
    },
)
async def describe_session_flows(
    integration_type: str,
    current_client: CurrentClient,
    service: SessionServiceDep,
    tool_code: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=TOOL_CODE_MAX_LENGTH,
            description="A tool serving the type; its default when omitted.",
        ),
    ] = None,
) -> FlowCatalogResponse:
    """Return the flows of one of the type's tools.

    Each flow lists the inputs to ask the holder for (label, input
    type, format, masking), whether the holder is redirected to sign
    in, the outputs a session returns, and the field keys that can be
    requested by id. Build the verification screen from this instead
    of hard-coding a provider.
    """
    return await service.describe_flows(
        current_client, integration_type, tool_code
    )


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

    ``consent`` is required: record the holder's consent on your side
    first and relay it here. The session runs straight away. It comes
    back ``awaiting_input`` when a step needs something the holder must
    type (listed in ``awaiting_inputs``), ``awaiting_redirect`` with
    ``next_action.url`` when the holder must sign in with the provider,
    ``completed`` with an ``outcome``, or ``failed`` with a
    ``failure_code``.
    """
    return await service.start(current_client, user_uuid, request)


@router.post(
    "/sessions/authorization-callback",
    response_model=SessionResponse,
    summary="Relay the provider's sign-in callback",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No sign-in is pending for this state",
        },
    },
)
async def complete_session_authorization(
    request: SessionAuthorizationCallback,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Finish a sign-in and continue the session.

    Send the ``state`` and ``code`` (or ``error``) your redirect URI
    received. ``state`` identifies the session and works once.
    """
    return await service.complete_authorization(current_client, request)


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


@router.delete(
    "/sessions/{session_id}/result",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Erase a session's result once used",
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def erase_session_result(
    session_id: uuid.UUID,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> None:
    """Delete the attributes now instead of at ``data_expires_at``.

    Call this once the values are saved where they are needed, so
    personal data is not kept longer than its purpose requires.
    """
    await service.erase_result(current_client, session_id)


@router.post(
    "/sessions/{session_id}/consent-withdrawal",
    response_model=SessionResponse,
    summary="Record that the holder withdrew consent",
    responses=_NOT_FOUND,
)
async def withdraw_session_consent(
    session_id: uuid.UUID,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> SessionResponse:
    """Stop the session and delete everything collected for it.

    An unfinished session is cancelled; inputs and the result are
    deleted; the withdrawal time is recorded. Safe to repeat.
    """
    return await service.withdraw_consent(current_client, session_id)


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


@router.get(
    "/kyc/verified-users",
    response_model=Page[VerifiedUserResponse],
    summary="List users whose KYC succeeded",
)
async def list_verified_users(
    filters: VerifiedUserQuery,
    current_client: CurrentClient,
    service: SessionServiceDep,
) -> Page[VerifiedUserResponse]:
    """Return your users whose identity verification succeeded.

    One item per user: their most recent session that completed with
    outcome `verified`, newest first. `total` counts users. Filter by
    `integration_type`, `flow`, `verified_after`, or a single
    `user_uuid`; without filters, every verified user is returned.
    Contains ids only; read a session's `/result` for its attributes.
    """
    return await service.list_verified_users(current_client, filters)
