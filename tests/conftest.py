"""Shared pytest fixtures."""

import os
from collections.abc import AsyncIterator

# Settings are read when app.main is imported, so the environment must
# be prepared first. Values are assigned (not defaulted) so a developer's
# .env file cannot change test behaviour. The MySQL URL is never
# connected to: the session dependency is replaced with SQLite below.
os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test"
os.environ["JWT_SECRET_KEY"] = "test-signing-key-" + "x" * 32
os.environ["SUPER_ADMIN_BOOTSTRAP_TOKEN"] = "test-bootstrap-token-" + "y" * 32
os.environ["LOGIN_MAX_FAILED_ATTEMPTS"] = "5"
os.environ["API_KEY_HASH_SECRET"] = "test-api-key-secret-" + "z" * 32
# A fixed, publicly known Fernet key: fine for tests, never for real data.
os.environ["CONNECTION_ENCRYPTION_KEYS"] = (
    "aW50ZWdyYXRpb24tc2VydmljZS10ZXN0LWtleS0zMmI="
)
os.environ["CONNECTOR_TIMEOUT_SECONDS"] = "0.5"
os.environ["LOG_JSON"] = "false"

import pytest  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

# Model modules are imported so Base.metadata knows every table.
import app.features.client_integrations.models  # noqa: E402, F401
import app.features.clients.models  # noqa: E402, F401
import app.features.integration_tools.models  # noqa: E402, F401
import app.features.integration_types.models  # noqa: E402, F401
import app.features.super_admins.models  # noqa: E402, F401
import app.features.user_connections.models  # noqa: E402, F401
from app.core.database import Base, get_db_session  # noqa: E402
from app.main import create_app  # noqa: E402

BOOTSTRAP_TOKEN = os.environ["SUPER_ADMIN_BOOTSTRAP_TOKEN"]


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # StaticPool keeps one connection open, so every session sees the
    # same in-memory database for the duration of the test.
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    application = create_app()

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = get_test_db_session
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # LifespanManager runs startup and shutdown so lifespan state
    # (engine, HTTP client) is populated exactly as in production.
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http_client:
            yield http_client


@pytest.fixture
async def super_admin_headers(client: AsyncClient) -> dict[str, str]:
    """Register a super admin through bootstrap and return auth headers."""
    credentials = {
        "email": "fixture.admin@example.com",
        "password": "fixture admin passphrase",
    }
    await client.post(
        "/api/v1/super-admins/register",
        json={**credentials, "full_name": "Fixture Admin"},
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )
    login_response = await client.post(
        "/api/v1/super-admins/login", json=credentials
    )
    access_token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}
