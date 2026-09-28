"""Versioned API router that collects every feature's routes.

Each feature exposes its own ``APIRouter``; this module is the single
place where they are mounted under ``/api/v1``. To add a feature::

    from app.features.verifications.router import (
        router as verifications_router,
    )

    api_v1_router.include_router(verifications_router)
"""

from fastapi import APIRouter, status

from app.features.super_admins.router import router as super_admins_router
from app.shared.schemas import ErrorResponse

api_v1_router = APIRouter(
    prefix="/api/v1",
    # Documented once here so every versioned route shows the shared
    # error envelope in OpenAPI.
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)

api_v1_router.include_router(super_admins_router)
