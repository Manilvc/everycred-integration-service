import copy
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.models import utc_now
from app.features.sessions.models import IntegrationSession
from tests.features.sessions.conftest import (
    AADHAAR,
    OTP,
    TOOL_DEFINITION,
    FakeProvider,
    consent,
    session_url,
    start,
)

TOOL_URL = "/v1/integration-tools/acme-identity"
FIELDS = [
    {"flow": "aadhaar_otp", "key": "full_name", "label": "Full name"},
    {"flow": "aadhaar_otp", "key": "dob", "label": "Date of birth"},
    {"flow": "employee_profile", "key": "work_email", "label": "Email"},
]


async def save_tool_with_specs(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    definition = copy.deepcopy(TOOL_DEFINITION)
    definition["fields"] = FIELDS
    definition["connector_config"]["flows"]["aadhaar_otp"]["input_specs"] = {
        "id_number": {
            "label": "Aadhaar number",
            "input_type": "number",
            "pattern": "[0-9]{12}",
            "is_masked": True,
        },
        "otp": {"label": "OTP", "input_type": "otp", "max_length": 6},
    }
    saved = await client.put(TOOL_URL, json=definition, headers=setup["admin"])
    assert saved.status_code == 200, saved.text
    return saved.json()


async def field_ids(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, str]:
    catalog = await client.get(
        "/v1/client/integration-types/confirm/flows", headers=setup["key"]
    )
    flow = next(
        flow
        for flow in catalog.json()["flows"]
        if flow["name"] == "aadhaar_otp"
    )
    return {field["key"]: field["id"] for field in flow["fields"]}


async def test_session_requires_consent(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    response = await client.post(
        "/v1/client/users/44444444-4444-4444-8444-444444444444/sessions",
        json={"integration_type": "confirm", "flow": "aadhaar_otp"},
        headers=setup["key"],
    )

    assert response.status_code == 422


async def test_stale_consent_is_rejected_before_any_call(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    stale = (utc_now() - timedelta(hours=1)).isoformat()

    response = await start(
        client,
        setup,
        body={"consent": consent(consented_at=stale)},
        id_number=AADHAAR,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_not_recent"
    assert provider.requests == []


async def test_consent_record_is_kept_with_the_session(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR)

    recorded = started.json()["consent"]
    assert recorded["purpose"] == (
        "Verify your identity to receive your credential"
    )
    assert recorded["notice_version"] == "2026-10-01"
    assert recorded["reference"] == "consent-7"
    assert recorded["withdrawn_at"] is None


async def test_flow_catalog_describes_inputs_and_fields(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool_with_specs(client, setup)

    response = await client.get(
        "/v1/client/integration-types/confirm/flows", headers=setup["key"]
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool_code"] == "acme-identity"
    flow = next(
        flow for flow in body["flows"] if flow["name"] == "aadhaar_otp"
    )
    assert flow["kind"] == "input"
    assert [item["name"] for item in flow["inputs"]] == ["id_number", "otp"]
    assert flow["inputs"][0]["label"] == "Aadhaar number"
    assert flow["inputs"][0]["is_masked"] is True
    assert flow["inputs"][1]["input_type"] == "otp"
    assert sorted(field["key"] for field in flow["fields"]) == [
        "dob",
        "full_name",
    ]


async def test_inputs_breaking_the_spec_are_rejected_without_echo(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    await save_tool_with_specs(client, setup)

    response = await start(client, setup, id_number="12345")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_inputs"
    assert "id_number" in error["message"]
    assert "12345" not in response.text
    assert provider.requests == []


async def test_requested_fields_limit_what_is_kept(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool_with_specs(client, setup)
    ids = await field_ids(client, setup)

    started = await start(
        client,
        setup,
        body={"requested_fields": [ids["full_name"]]},
        id_number=AADHAAR,
        otp=OTP,
    )
    session = started.json()
    result = await client.get(
        session_url(session["id"], "result"), headers=setup["key"]
    )

    assert session["status"] == "completed", session
    assert session["result_attributes"] == []
    assert session["result_fields"] == [ids["full_name"]]
    body = result.json()
    assert body["attributes"] == {}
    assert body["fields"] == [
        {
            "field_id": ids["full_name"],
            "key": "full_name",
            "value": "Asha Verma",
        }
    ]


async def test_requested_attributes_drop_the_rest(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(
        client,
        setup,
        body={"requested_attributes": ["date_of_birth"]},
        id_number=AADHAAR,
        otp=OTP,
    )

    assert started.json()["result_attributes"] == ["date_of_birth"]


async def test_unknown_requested_attribute_or_field_is_rejected(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await save_tool_with_specs(client, setup)
    catalog = await client.get(
        "/v1/client/integration-types/confirm/flows", headers=setup["key"]
    )
    other_flow_field = next(
        field["id"]
        for flow in catalog.json()["flows"]
        if flow["name"] == "employee_profile"
        for field in flow["fields"]
    )

    attribute = await start(
        client, setup, body={"requested_attributes": ["pan"]}
    )
    wrong_flow = await start(
        client, setup, body={"requested_fields": [other_flow_field]}
    )

    assert attribute.json()["error"]["code"] == "unknown_attributes"
    assert wrong_flow.json()["error"]["code"] == "unknown_fields"


async def test_erasing_the_result_deletes_it(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR, otp=OTP)
    session_id = started.json()["id"]

    erased = await client.delete(
        session_url(session_id, "result"), headers=setup["key"]
    )
    again = await client.delete(
        session_url(session_id, "result"), headers=setup["key"]
    )
    result = await client.get(
        session_url(session_id, "result"), headers=setup["key"]
    )

    assert erased.status_code == 204
    assert again.status_code == 204
    assert result.status_code == 410


async def test_withdrawing_consent_cancels_and_records_it(
    client: AsyncClient,
    setup: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started = await start(client, setup, id_number=AADHAAR)
    session_id = started.json()["id"]

    withdrawn = await client.post(
        session_url(session_id, "consent-withdrawal"), headers=setup["key"]
    )
    repeated = await client.post(
        session_url(session_id, "consent-withdrawal"), headers=setup["key"]
    )

    body = withdrawn.json()
    assert body["status"] == "cancelled"
    assert body["consent"]["withdrawn_at"] is not None
    assert (
        repeated.json()["consent"]["withdrawn_at"]
        == (body["consent"]["withdrawn_at"])
    )
    async with session_factory() as db:
        row = await db.scalar(select(IntegrationSession))
        assert row is not None
        assert row.state_secret_reference is None


async def test_withdrawing_consent_after_completion_erases_result(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR, otp=OTP)
    session_id = started.json()["id"]

    await client.post(
        session_url(session_id, "consent-withdrawal"), headers=setup["key"]
    )
    result = await client.get(
        session_url(session_id, "result"), headers=setup["key"]
    )

    assert result.status_code == 410
