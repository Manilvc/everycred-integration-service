from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def docs_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[pytest.MonkeyPatch]:
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


async def fetch_status(path: str) -> int:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as http_client:
        return (await http_client.get(path)).status_code


@pytest.mark.parametrize("path", ["/redoc", "/docs", "/openapi.json"])
async def test_docs_are_served_when_enabled(
    docs_environment: pytest.MonkeyPatch, path: str
) -> None:
    docs_environment.setenv("ENABLE_DOCS", "true")

    assert await fetch_status(path) == 200


@pytest.mark.parametrize("path", ["/redoc", "/docs", "/openapi.json"])
async def test_docs_are_hidden_in_production(
    docs_environment: pytest.MonkeyPatch, path: str
) -> None:
    docs_environment.setenv("ENABLE_DOCS", "true")
    docs_environment.setenv("ENVIRONMENT", "production")
    # Production refuses to start without the AWS secret store. No AWS
    # call is made at startup, so these values are never used.
    docs_environment.setenv("SECRET_STORE_BACKEND", "aws")
    docs_environment.setenv("AWS_REGION", "ap-south-1")
    docs_environment.setenv("SECRETS_KMS_KEY_ID", "alias/test")

    assert await fetch_status(path) == 404


def test_every_tag_is_described_and_grouped() -> None:
    schema = create_app().openapi()
    used_tags = {
        tag
        for operations in schema["paths"].values()
        for operation in operations.values()
        for tag in operation.get("tags", [])
    }
    described_tags = {tag["name"] for tag in schema["tags"]}
    grouped_tags = {
        tag for group in schema["x-tagGroups"] for tag in group["tags"]
    }

    assert used_tags == described_tags == grouped_tags


def test_operation_ids_are_unique_and_url_safe() -> None:
    schema = create_app().openapi()
    operation_ids = [
        operation["operationId"]
        for operations in schema["paths"].values()
        for operation in operations.values()
    ]

    assert len(operation_ids) == len(set(operation_ids))
    assert all(" " not in operation_id for operation_id in operation_ids)
