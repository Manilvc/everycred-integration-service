import pytest
from httpx import AsyncClient

from tests.conftest import BOOTSTRAP_TOKEN

REGISTER_URL = "/api/v1/super-admins/register"
LOGIN_URL = "/api/v1/super-admins/login"

FIRST_ADMIN = {
    "email": "First.Admin@Example.com",
    "full_name": "  First Admin  ",
    "password": "correct horse battery staple",
}
SECOND_ADMIN = {
    "email": "second.admin@example.com",
    "full_name": "Second Admin",
    "password": "another long passphrase",
}


@pytest.fixture
async def first_admin_token(client: AsyncClient) -> str:
    await client.post(
        REGISTER_URL,
        json=FIRST_ADMIN,
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )
    response = await client.post(
        LOGIN_URL,
        json={
            "email": FIRST_ADMIN["email"],
            "password": FIRST_ADMIN["password"],
        },
    )
    return response.json()["access_token"]


async def test_bootstrap_registration_creates_first_admin(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=FIRST_ADMIN,
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "first.admin@example.com"
    assert body["full_name"] == "First Admin"
    assert body["is_active"] is True
    assert "password" not in body
    assert "password_hash" not in body


async def test_registration_without_credentials_is_rejected(
    client: AsyncClient,
) -> None:
    response = await client.post(REGISTER_URL, json=FIRST_ADMIN)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_registration_with_wrong_bootstrap_token_is_rejected(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=FIRST_ADMIN,
        headers={"X-Bootstrap-Token": "not-the-real-token"},
    )

    assert response.status_code == 401


async def test_bootstrap_token_stops_working_after_first_admin(
    client: AsyncClient, first_admin_token: str
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=SECOND_ADMIN,
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bootstrap_closed"


async def test_signed_in_admin_can_register_another(
    client: AsyncClient, first_admin_token: str
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=SECOND_ADMIN,
        headers={"Authorization": f"Bearer {first_admin_token}"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == SECOND_ADMIN["email"]


async def test_duplicate_email_is_rejected_regardless_of_case(
    client: AsyncClient, first_admin_token: str
) -> None:
    duplicate = {**SECOND_ADMIN, "email": FIRST_ADMIN["email"].upper()}

    response = await client.post(
        REGISTER_URL,
        json=duplicate,
        headers={"Authorization": f"Bearer {first_admin_token}"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "super_admin_already_exists"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", "too-short"),
        ("email", "not-an-email"),
        ("full_name", "   "),
    ],
)
async def test_invalid_registration_details_are_rejected(
    client: AsyncClient, field: str, value: str
) -> None:
    response = await client.post(
        REGISTER_URL,
        json={**FIRST_ADMIN, field: value},
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == ["body", field]
    assert value not in response.text or value.strip() == ""


async def test_unknown_fields_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER_URL,
        json={**FIRST_ADMIN, "is_active": False},
        headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
    )

    assert response.status_code == 422


async def test_bootstrap_token_sent_as_bearer_explains_the_fix(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=FIRST_ADMIN,
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
    )

    assert response.status_code == 401
    assert "X-Bootstrap-Token header" in response.json()["error"]["message"]


async def test_bootstrap_header_wins_over_stale_bearer_token(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=FIRST_ADMIN,
        headers={
            "Authorization": "Bearer an.expired.token",
            "X-Bootstrap-Token": BOOTSTRAP_TOKEN,
        },
    )

    assert response.status_code == 201


async def test_signed_in_admin_sending_bootstrap_header_gets_clear_error(
    client: AsyncClient, first_admin_token: str
) -> None:
    response = await client.post(
        REGISTER_URL,
        json=SECOND_ADMIN,
        headers={
            "Authorization": f"Bearer {first_admin_token}",
            "X-Bootstrap-Token": BOOTSTRAP_TOKEN,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bootstrap_closed"
