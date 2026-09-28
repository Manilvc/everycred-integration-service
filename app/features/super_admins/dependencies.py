"""Dependency providers and access checks for super admin routes."""

import hmac
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, SettingsDep
from app.core.database import DbSession
from app.core.exceptions import AuthenticationError
from app.core.security import decode_access_token
from app.features.super_admins.exceptions import BootstrapClosedError
from app.features.super_admins.models import SuperAdmin
from app.features.super_admins.repository import SuperAdminRepository
from app.features.super_admins.service import SuperAdminService

# auto_error=False lets us return the standard error envelope instead of
# FastAPI's default 403 body when the header is missing.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Access token from POST /api/v1/super-admins/login.",
)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
]


def get_super_admin_service(
    session: DbSession, settings: SettingsDep
) -> SuperAdminService:
    """Build a super admin service bound to the request's session."""
    return SuperAdminService(
        session=session,
        repository=SuperAdminRepository(session),
        settings=settings,
    )


SuperAdminServiceDep = Annotated[
    SuperAdminService, Depends(get_super_admin_service)
]


async def _resolve_bearer_token(
    credentials: HTTPAuthorizationCredentials,
    service: SuperAdminService,
    settings: Settings,
) -> SuperAdmin:
    claims = decode_access_token(credentials.credentials, settings)
    return await service.get_authenticated_super_admin(claims)


async def get_current_super_admin(
    credentials: BearerCredentials,
    service: SuperAdminServiceDep,
    settings: SettingsDep,
) -> SuperAdmin:
    """Return the super admin identified by the request's bearer token.

    Raises:
        AuthenticationError: No token was sent, or it is invalid.
    """
    if credentials is None:
        raise AuthenticationError("Authentication is required.")
    return await _resolve_bearer_token(credentials, service, settings)


CurrentSuperAdmin = Annotated[SuperAdmin, Depends(get_current_super_admin)]


async def authorize_registration(
    credentials: BearerCredentials,
    service: SuperAdminServiceDep,
    settings: SettingsDep,
    x_bootstrap_token: Annotated[
        str | None,
        Header(
            max_length=256,
            description="Only for registering the first super admin.",
        ),
    ] = None,
) -> SuperAdmin | None:
    """Decide whether the caller may register a super admin.

    A signed-in super admin may always register another. Without a
    token, the ``X-Bootstrap-Token`` header is accepted only while no
    super admin exists yet, which is how the first account is created.

    Returns:
        The registering super admin, or None for bootstrap registration.

    Raises:
        AuthenticationError: Neither credential was sent, or the
            bootstrap token is wrong or bootstrapping is not configured.
        BootstrapClosedError: The bootstrap token is valid but a super
            admin already exists.
    """
    if credentials is not None:
        return await _resolve_bearer_token(credentials, service, settings)

    if x_bootstrap_token is None:
        raise AuthenticationError("Authentication is required.")

    expected_token = settings.super_admin_bootstrap_token
    # compare_digest keeps the comparison time independent of how many
    # leading characters match.
    if expected_token is None or not hmac.compare_digest(
        x_bootstrap_token.encode(), expected_token.get_secret_value().encode()
    ):
        raise AuthenticationError("Invalid bootstrap token.")

    if await service.has_any_super_admin():
        raise BootstrapClosedError
    return None


RegisteringSuperAdmin = Annotated[
    SuperAdmin | None, Depends(authorize_registration)
]
