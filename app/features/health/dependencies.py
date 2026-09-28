"""Dependency providers for the health feature."""

from typing import Annotated

from fastapi import Depends

from app.core.database import DbEngine
from app.features.health.service import HealthService


def get_health_service(db_engine: DbEngine) -> HealthService:
    """Build a health service bound to the application's engine."""
    return HealthService(db_engine=db_engine)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
