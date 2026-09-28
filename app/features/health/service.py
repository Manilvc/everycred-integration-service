"""Checks that decide whether the service can accept traffic."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import ping_database
from app.features.health.schemas import ComponentStatus, ReadinessResponse

logger = logging.getLogger(__name__)

# Keep below the orchestrator's probe timeout so the endpoint answers
# "not ready" instead of the probe itself timing out.
DATABASE_CHECK_TIMEOUT_SECONDS = 2.0


class HealthService:
    """Runs readiness checks against the service's dependencies.

    Attributes:
        db_engine: Engine used to confirm the database is reachable.
    """

    def __init__(self, db_engine: AsyncEngine) -> None:
        self.db_engine = db_engine

    async def check_readiness(self) -> ReadinessResponse:
        """Check every dependency and report which ones are available.

        Failures are logged here with full detail. The response only
        says which component failed, so probe callers never see
        connection strings or driver errors.
        """
        components = {"database": await self._check_database()}
        is_ready = all(
            component_status is ComponentStatus.OK
            for component_status in components.values()
        )
        return ReadinessResponse(is_ready=is_ready, components=components)

    async def _check_database(self) -> ComponentStatus:
        try:
            async with asyncio.timeout(DATABASE_CHECK_TIMEOUT_SECONDS):
                await ping_database(self.db_engine)
        except Exception:
            # Any failure means "not ready"; the probe must answer rather
            # than crash, whatever the driver raised.
            logger.exception("Database readiness check failed")
            return ComponentStatus.UNAVAILABLE
        return ComponentStatus.OK
