import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.exceptions import NotFoundError
from app.shared.pagination import Pagination


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    async def raise_not_found() -> None:
        raise NotFoundError("Verification was not found.")

    async def raise_unexpected_error() -> None:
        raise RuntimeError("database password is hunter2")

    async def list_items(pagination: Pagination) -> dict[str, int]:
        return pagination.model_dump()

    app.add_api_route("/test/not-found", raise_not_found)
    app.add_api_route("/test/crash", raise_unexpected_error)
    app.add_api_route("/test/items", list_items)
    return app


async def test_app_error_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/test/not-found")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "Verification was not found."
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_unexpected_error_hides_internal_details(
    client: AsyncClient,
) -> None:
    response = await client.get("/test/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "X-Request-ID" in response.headers


async def test_validation_error_does_not_echo_input(
    client: AsyncClient,
) -> None:
    response = await client.get("/test/items", params={"limit": "secret"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "limit"]
    assert "secret" not in response.text


async def test_pagination_defaults_are_applied(client: AsyncClient) -> None:
    response = await client.get("/test/items")

    assert response.json() == {"limit": 20, "offset": 0}
