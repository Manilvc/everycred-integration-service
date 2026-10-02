from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.security import TOKEN_AUDIENCE, create_access_token
from tests.conftest import BOOTSTRAP_TOKEN

REGISTER_URL = "/v1/super-admins/register"
LOGIN_URL = "/v1/super-admins/login"
ME_URL = "/v1/super-admins/me"

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
async def registered_admin_id(client: AsyncClient) -> str:
    response = await client.post(
        REGISTER_URL,
        json={
            "email": ADMIN_EMAIL,
            "full_name": "Admin",
            "password": ADMIN_PASSWORD,
        },
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )
    return response.json()["id"]


async def log_in(client: AsyncClient, password: str, email: str = ADMIN_EMAIL):
    return await client.post(
        LOGIN_URL, json={"email": email, "password": password}
    )


async def test_login_returns_bearer_token(
    client: AsyncClient, registered_admin_id: str
) -> None:
    response = await log_in(client, ADMIN_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert 0 < body["expires_in"] <= 30 * 60
    assert response.headers["Cache-Control"] == "no-store"


async def test_token_gives_access_to_profile(
    client: AsyncClient, registered_admin_id: str
) -> None:
    token = (await log_in(client, ADMIN_PASSWORD)).json()["access_token"]

    response = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == registered_admin_id
    assert response.json()["last_login_at"] is not None


async def test_login_email_is_case_insensitive(
    client: AsyncClient, registered_admin_id: str
) -> None:
    response = await log_in(client, ADMIN_PASSWORD, email=ADMIN_EMAIL.upper())

    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical(
    client: AsyncClient, registered_admin_id: str
) -> None:
    wrong_password = await log_in(client, "wrong password here")
    unknown_email = await log_in(
        client, ADMIN_PASSWORD, email="nobody@example.com"
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert (
        wrong_password.json()["error"]["message"]
        == unknown_email.json()["error"]["message"]
    )
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


async def test_account_locks_after_repeated_failures(
    client: AsyncClient, registered_admin_id: str
) -> None:
    for _ in range(get_settings().login_max_failed_attempts):
        await log_in(client, "wrong password here")

    response = await log_in(client, ADMIN_PASSWORD)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


async def test_successful_login_resets_failed_attempts(
    client: AsyncClient, registered_admin_id: str
) -> None:
    max_attempts = get_settings().login_max_failed_attempts
    for _ in range(max_attempts - 1):
        await log_in(client, "wrong password here")
    await log_in(client, ADMIN_PASSWORD)
    for _ in range(max_attempts - 1):
        await log_in(client, "wrong password here")

    response = await log_in(client, ADMIN_PASSWORD)

    assert response.status_code == 200


async def test_profile_requires_token(client: AsyncClient) -> None:
    response = await client.get(ME_URL)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_malformed_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        ME_URL, headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401


async def test_expired_token_is_rejected(
    client: AsyncClient, registered_admin_id: str
) -> None:
    settings = get_settings()
    issued_at = datetime.now(UTC) - timedelta(hours=2)
    expired_token = jwt.encode(
        {
            "sub": registered_admin_id,
            "role": "super_admin",
            "iss": settings.service_name,
            "aud": TOKEN_AUDIENCE,
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=30),
            "jti": "expired",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401


async def test_token_signed_with_another_key_is_rejected(
    client: AsyncClient, registered_admin_id: str
) -> None:
    settings = get_settings()
    forged_settings = settings.model_copy(
        update={"jwt_secret_key": type(settings.jwt_secret_key)("z" * 48)}
    )
    forged = create_access_token(
        registered_admin_id, "super_admin", forged_settings
    )

    response = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {forged.token}"}
    )

    assert response.status_code == 401


async def test_token_for_another_role_is_forbidden(
    client: AsyncClient, registered_admin_id: str
) -> None:
    other_role_token = create_access_token(
        registered_admin_id, "tenant_admin", get_settings()
    )

    response = await client.get(
        ME_URL, headers={"Authorization": f"Bearer {other_role_token.token}"}
    )

    assert response.status_code == 403
