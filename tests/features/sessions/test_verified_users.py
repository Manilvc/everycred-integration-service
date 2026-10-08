from typing import Any

from httpx import AsyncClient

from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.sessions.conftest import AADHAAR, OTP, USER, sessions_url

VERIFIED_URL = "/v1/client/kyc/verified-users"
OTHER_USER = "55555555-5555-4555-8555-555555555555"
THIRD_USER = "66666666-6666-4666-8666-666666666666"


async def run_kyc(
    client: AsyncClient,
    setup: dict[str, Any],
    user: str,
    otp: str | None = OTP,
) -> dict[str, Any]:
    """Start an Aadhaar OTP session; with ``otp`` None it stays open."""
    inputs = {"id_number": AADHAAR}
    if otp is not None:
        inputs["otp"] = otp
    response = await client.post(
        sessions_url(user),
        json={
            "integration_type": "confirm",
            "flow": "aadhaar_otp",
            "inputs": inputs,
        },
        headers=setup["key"],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def verified(
    client: AsyncClient, setup: dict[str, Any], **params: Any
) -> dict[str, Any]:
    response = await client.get(
        VERIFIED_URL, params=params, headers=setup["key"]
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_lists_only_users_whose_kyc_succeeded(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    passed = await run_kyc(client, setup, USER)
    await run_kyc(client, setup, OTHER_USER, otp="000000")  # not verified
    await run_kyc(client, setup, THIRD_USER, otp=None)  # awaiting OTP
    await client.post(
        sessions_url(THIRD_USER),
        json={
            "integration_type": "gather",
            "flow": "employee_profile",
            "inputs": {"employee_id": "E-1001"},
        },
        headers=setup["key"],
    )

    body = await verified(client, setup)

    assert body["total"] == 1
    (item,) = body["items"]
    assert item == {
        "user_uuid": USER,
        "verified_at": passed["completed_at"],
        "session_id": passed["id"],
        "integration_type": "confirm",
        "tool_code": "acme-identity",
        "flow": "aadhaar_otp",
    }


async def test_each_user_appears_once_with_their_latest_kyc(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await run_kyc(client, setup, USER)
    latest = await run_kyc(client, setup, USER)

    body = await verified(client, setup)

    assert body["total"] == 1
    assert body["items"][0]["session_id"] == latest["id"]


async def test_newest_verification_comes_first(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await run_kyc(client, setup, USER)
    await run_kyc(client, setup, OTHER_USER)

    body = await verified(client, setup)

    assert [item["user_uuid"] for item in body["items"]] == [
        OTHER_USER,
        USER,
    ]
    paged = await verified(client, setup, limit=1, offset=1)
    assert [item["user_uuid"] for item in paged["items"]] == [USER]
    assert paged["total"] == 2


async def test_filters(client: AsyncClient, setup: dict[str, Any]) -> None:
    first = await run_kyc(client, setup, USER)
    await run_kyc(client, setup, OTHER_USER)

    one_user = await verified(client, setup, user_uuid=USER)
    by_type = await verified(client, setup, integration_type="confirm")
    other_type = await verified(client, setup, integration_type="gather")
    other_flow = await verified(client, setup, flow="employee_profile")
    since_first = await verified(
        client, setup, verified_after=first["completed_at"]
    )
    future = await verified(
        client, setup, verified_after="2999-01-01T00:00:00"
    )

    assert [item["user_uuid"] for item in one_user["items"]] == [USER]
    assert by_type["total"] == 2
    assert other_type["total"] == 0
    assert other_flow["total"] == 0
    assert since_first["total"] == 2
    # A time without a timezone is read as UTC instead of failing.
    assert future["total"] == 0


async def test_other_clients_do_not_see_these_users(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await run_kyc(client, setup, USER)
    other = await create_client(client, setup["admin"], code="other-portal")
    other_key = await issue_api_key(client, setup["admin"], other["id"])

    response = await client.get(
        VERIFIED_URL, headers={"X-API-Key": other_key["api_key"]}
    )

    assert response.json()["items"] == []


async def test_requires_an_api_key(client: AsyncClient) -> None:
    response = await client.get(VERIFIED_URL)

    assert response.status_code == 401
