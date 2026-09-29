from httpx import AsyncClient


async def test_valid_request_id_is_echoed_back(client: AsyncClient) -> None:
    response = await client.get(
        "/health/live", headers={"X-Request-ID": "abc-123"}
    )

    assert response.headers["X-Request-ID"] == "abc-123"


async def test_unsafe_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get(
        "/health/live", headers={"X-Request-ID": "bad id\nforged-log-line"}
    )

    assert response.headers["X-Request-ID"] != "bad id\nforged-log-line"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_request_id_is_generated_when_missing(
    client: AsyncClient,
) -> None:
    response = await client.get("/health/live")

    assert len(response.headers["X-Request-ID"]) == 32
