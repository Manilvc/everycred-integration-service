from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
)
from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.sessions.conftest import AADHAAR, OTP, FakeProvider, start

ADMIN_FIELDS_URL = "/v1/integration-tools/acme-identity/fields"
CLIENT_FIELDS_URL = "/v1/client/integration-tools/acme-identity/fields"


def keys_by_flow(page: dict[str, Any]) -> dict[str, dict[str, str]]:
    by_flow: dict[str, dict[str, str]] = {}
    for field in page["items"]:
        by_flow.setdefault(field["flow"], {})[field["key"]] = field[
            "value_type"
        ]
    return by_flow


async def test_completed_session_records_provider_keys_not_values(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await start(client, setup, id_number=AADHAAR, otp=OTP)

    response = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert response.status_code == 200, response.text
    # Keys of the final step's response data, with their JSON types.
    assert keys_by_flow(response.json()) == {
        "aadhaar_otp": {
            "dob": "string",
            "full_name": "string",
            "status": "string",
        }
    }
    assert "Asha" not in response.text
    assert "1990-01-01" not in response.text


async def test_nested_keys_use_dot_paths(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await start(
        client,
        setup,
        flow="employee_profile",
        integration_type="gather",
        employee_id="E-1001",
    )

    response = await client.get(
        CLIENT_FIELDS_URL,
        params={"flow": "employee_profile"},
        headers=setup["key"],
    )

    assert keys_by_flow(response.json()) == {
        "employee_profile": {
            "employee_code": "string",
            "org.department": "string",
            "work_email": "string",
        }
    }


async def test_keys_are_recorded_once_and_refreshed(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    await start(client, setup, id_number=AADHAAR, otp=OTP)
    first = (await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])).json()

    def with_extra_field(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/otp/submit"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": "valid",
                        "full_name": "Asha Verma",
                        "dob": 19900101,
                        "address": {"zip": "411001", "dist": "Pune"},
                    },
                },
            )
        return provider_default(request)

    provider_default = provider.reply
    provider.reply = with_extra_field
    await start(client, setup, id_number=AADHAAR, otp=OTP)
    second = (
        await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])
    ).json()

    assert first["total"] == 3
    assert keys_by_flow(second) == {
        "aadhaar_otp": {
            "address.dist": "string",
            "address.zip": "string",
            "dob": "number",
            "full_name": "string",
            "status": "string",
        }
    }
    full_name = next(f for f in second["items"] if f["key"] == "full_name")
    assert full_name["last_seen_at"] >= full_name["first_seen_at"]


async def test_not_verified_sessions_record_keys_too(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await start(client, setup, id_number=AADHAAR, otp="000000")

    response = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert "full_name" in keys_by_flow(response.json())["aadhaar_otp"]


async def test_unfinished_and_failed_sessions_record_nothing(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    await start(client, setup, id_number=AADHAAR)
    provider.reply = lambda _: httpx.Response(502, text="down")
    await start(client, setup, id_number=AADHAAR, otp=OTP)

    response = await client.get(ADMIN_FIELDS_URL, headers=setup["admin"])

    assert response.json()["items"] == []


async def test_recording_failure_does_not_fail_the_session(
    client: AsyncClient,
    setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_record(*args: Any, **kwargs: Any) -> int:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        IntegrationToolFieldRepository, "record", broken_record
    )

    response = await start(client, setup, id_number=AADHAAR, otp=OTP)

    assert response.status_code == 201
    assert response.json()["outcome"] == "verified"


async def test_fields_of_tools_outside_the_client_look_missing(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    other = await create_client(client, setup["admin"], code="other-portal")
    other_key = await issue_api_key(client, setup["admin"], other["id"])

    response = await client.get(
        CLIENT_FIELDS_URL, headers={"X-API-Key": other_key["api_key"]}
    )
    unknown = await client.get(
        "/v1/integration-tools/no-such-tool/fields", headers=setup["admin"]
    )

    assert response.status_code == 404
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "integration_tool_not_found"


async def test_field_routes_require_auth(client: AsyncClient) -> None:
    assert (await client.get(ADMIN_FIELDS_URL)).status_code == 401
    assert (await client.get(CLIENT_FIELDS_URL)).status_code == 401
