"""Liveness and readiness probes for the orchestrator.

These routes are mounted at the root, outside ``/v1``, because
load balancers and Kubernetes probes should not depend on API
versioning.
"""

from fastapi import APIRouter, Response, status

from app.features.health.dependencies import HealthServiceDep
from app.features.health.schemas import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Check that the process is running",
)
async def check_liveness() -> LivenessResponse:
    """Return OK whenever the process can serve requests.

    This probe deliberately checks nothing external, so a database
    outage does not cause the orchestrator to restart healthy pods.
    """
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Check that dependencies are reachable",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "At least one dependency is unavailable",
        },
    },
)
async def check_readiness(
    response: Response,
    health_service: HealthServiceDep,
) -> ReadinessResponse:
    """Report whether the service can handle traffic right now."""
    readiness = await health_service.check_readiness()
    if not readiness.is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return readiness
