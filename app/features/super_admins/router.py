"""HTTP routes for super admin registration, login, and identity."""

from fastapi import APIRouter, Response, status

from app.core.models import utc_now
from app.features.super_admins.dependencies import (
    CurrentSuperAdmin,
    RegisteringSuperAdmin,
    SuperAdminServiceDep,
)
from app.features.super_admins.schemas import (
    AccessTokenResponse,
    SuperAdminLogin,
    SuperAdminRegistration,
    SuperAdminResponse,
)
from app.shared.schemas import ErrorResponse

router = APIRouter(prefix="/super-admins", tags=["Super Admins"])

_UNAUTHORIZED = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": "Missing or invalid credentials",
    }
}


@router.post(
    "/register",
    response_model=SuperAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a super admin",
    responses={
        **_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Bootstrap token used after setup",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Email already registered",
        },
    },
)
async def register_super_admin(
    registration: SuperAdminRegistration,
    registered_by: RegisteringSuperAdmin,
    service: SuperAdminServiceDep,
) -> SuperAdminResponse:
    """Create a super admin account.

    Send a super admin bearer token, or, for the very first account
    only, the ``X-Bootstrap-Token`` header.
    """
    super_admin = await service.register_super_admin(
        registration, registered_by=registered_by
    )
    return SuperAdminResponse.model_validate(super_admin)


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    summary="Log in as a super admin",
    responses=_UNAUTHORIZED,
)
async def log_in_super_admin(
    credentials: SuperAdminLogin,
    response: Response,
    service: SuperAdminServiceDep,
) -> AccessTokenResponse:
    """Exchange email and password for a bearer access token."""
    access_token = await service.log_in(credentials)
    # RFC 6749 section 5.1: token responses must not be cached.
    response.headers["Cache-Control"] = "no-store"
    expires_in_seconds = int(
        (access_token.expires_at - utc_now()).total_seconds()
    )
    return AccessTokenResponse(
        access_token=access_token.token,
        expires_in=expires_in_seconds,
        expires_at=access_token.expires_at,
    )


@router.get(
    "/me",
    response_model=SuperAdminResponse,
    summary="Get the signed-in super admin",
    responses=_UNAUTHORIZED,
)
async def get_current_super_admin_profile(
    current_super_admin: CurrentSuperAdmin,
) -> SuperAdminResponse:
    """Return the account that the bearer token belongs to."""
    return SuperAdminResponse.model_validate(current_super_admin)
