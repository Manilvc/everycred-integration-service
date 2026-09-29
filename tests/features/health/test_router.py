from fastapi import FastAPI
from httpx import AsyncClient

from app.features.health.dependencies import get_health_service
from app.features.health.schemas import ComponentStatus, ReadinessResponse


class FakeHealthService:
    def __init__(self, database_status: ComponentStatus) -> None:
        self.database_status = database_status

    async def check_readiness(self) -> ReadinessResponse:
        return ReadinessResponse(
            is_ready=self.database_status is ComponentStatus.OK,
            components={"database": self.database_status},
        )


async def test_liveness_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_returns_200_when_database_is_reachable(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_health_service] = lambda: FakeHealthService(
        ComponentStatus.OK
    )

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "is_ready": True,
        "components": {"database": "ok"},
    }


async def test_readiness_returns_503_when_database_is_unavailable(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_health_service] = lambda: FakeHealthService(
        ComponentStatus.UNAVAILABLE
    )

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["components"] == {"database": "unavailable"}
