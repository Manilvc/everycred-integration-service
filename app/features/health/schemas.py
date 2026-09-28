"""Response models for the health endpoints."""

from enum import StrEnum

from pydantic import BaseModel


class ComponentStatus(StrEnum):
    """Health of a single dependency."""

    OK = "ok"
    UNAVAILABLE = "unavailable"


class LivenessResponse(BaseModel):
    """Result of the liveness probe."""

    status: ComponentStatus = ComponentStatus.OK


class ReadinessResponse(BaseModel):
    """Result of the readiness probe, broken down by dependency."""

    is_ready: bool
    components: dict[str, ComponentStatus]
