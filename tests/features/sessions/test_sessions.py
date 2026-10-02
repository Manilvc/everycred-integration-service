import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.models import utc_now
from app.core.secret_store import LocalSecret
from app.features.sessions.models import IntegrationSession
from app.features.user_connections.models import IntegrationOperationLog
from app.features.webhooks import delivery as webhook_delivery
from app.features.webhooks.models import (
    ClientWebhook,
    DeliveryStatus,
    WebhookDelivery,
)
from app.worker import run_once
from tests.features.clients.conftest import create_client, issue_api_key
from tests.features.sessions.conftest import (
    AADHAAR,
    API_TOKEN,
    OTP,
    FakeProvider,
    session_url,
    sessions_url,
    start,
)


async def secret_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count()).select_from(LocalSecret)
        )


async def test_otp_flow_pauses_for_otp_then_verifies(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    started = await start(client, setup, id_number=AADHAAR)

    assert started.status_code == 201, started.text
    session = started.json()
    assert session["status"] == "awaiting_input"
    assert session["awaiting_inputs"] == ["otp"]
    assert session["purpose"] == "verification"
    assert session["reference"] == "invite-7"
    assert provider.paths() == ["/api/otp/generate"]
    assert json.loads(provider.requests[0].content) == {"id_number": AADHAAR}
    assert provider.requests[0].headers["Authorization"] == (
        f"Bearer {API_TOKEN}"
    )

    finished = await client.post(
        session_url(session["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=setup["key"],
    )

    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "verified"
    assert body["result_attributes"] == [
        "date_of_birth",
        "full_name",
        "provider_reference",
    ]
    assert body["data_expires_at"] is not None
    # The captured request id was sent to the second step.
    assert json.loads(provider.requests[1].content) == {
        "request_id": "req-42",
        "otp": OTP,
    }

    result = await client.get(
        session_url(session["id"], "result"), headers=setup["key"]
    )
    assert result.status_code == 200, result.text
    assert result.json()["attributes"] == {
        "full_name": "Asha Verma",
        "date_of_birth": "1990-01-01",
        "provider_reference": "req-42",
    }


async def test_status_responses_never_contain_inputs_or_attributes(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR)
    finished = await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=setup["key"],
    )
    polled = await client.get(
        session_url(started.json()["id"]), headers=setup["key"]
    )

    for response in (started, finished, polled):
        assert AADHAAR not in response.text
        assert OTP not in response.text
        assert "Asha" not in response.text


async def test_inputs_are_deleted_when_the_session_finishes(
    client: AsyncClient,
    setup: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Only the tool credentials are stored before the session.
    before = await secret_count(session_factory)
    started = await start(client, setup, id_number=AADHAAR)
    assert await secret_count(session_factory) == before + 1  # inputs

    await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=setup["key"],
    )

    # Inputs gone, result stored.
    assert await secret_count(session_factory) == before + 1
    async with session_factory() as session:
        stored = await session.get(
            IntegrationSession, uuid.UUID(started.json()["id"])
        )
        assert stored.state_secret_reference is None
        assert stored.result_secret_reference is not None
        secrets = (await session.scalars(select(LocalSecret))).all()
    assert all(AADHAAR not in secret.ciphertext for secret in secrets)


async def test_wrong_otp_completes_as_not_verified(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR)
    finished = await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": "000000"}},
        headers=setup["key"],
    )

    body = finished.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "not_verified"
    assert body["failure_code"] == "provider_rejected"
    assert body["result_attributes"] == []
    result = await client.get(
        session_url(body["id"], "result"), headers=setup["key"]
    )
    assert result.status_code == 200
    assert result.json()["attributes"] == {}


async def test_all_inputs_up_front_runs_to_completion(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    started = await start(client, setup, id_number=AADHAAR, otp=OTP)

    assert started.json()["status"] == "completed"
    assert started.json()["outcome"] == "verified"
    assert len(provider.requests) == 2


async def test_gather_flow_returns_mapped_attributes(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    started = await start(
        client,
        setup,
        flow="employee_profile",
        integration_type="gather",
        employee_id="E-1001",
    )

    body = started.json()
    assert body["status"] == "completed", body
    assert body["outcome"] == "gathered"
    assert provider.paths() == ["/api/employees/E-1001"]
    result = await client.get(
        session_url(body["id"], "result"), headers=setup["key"]
    )
    assert result.json()["attributes"] == {
        "email": "asha@example.com",
        "department": "Finance",
    }


async def test_client_field_mappings_override_outputs(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    saved = await client.put(
        "/v1/client/integrations/acme-identity",
        json={
            "field_mappings": {
                "employee_profile": {
                    "employee_number": "employee_code",
                    "email": "work_email",
                }
            }
        },
        headers=setup["key"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["flows"] == ["aadhaar_otp", "employee_profile"]

    started = await start(
        client,
        setup,
        flow="employee_profile",
        integration_type="gather",
        employee_id="E-1001",
    )
    result = await client.get(
        session_url(started.json()["id"], "result"), headers=setup["key"]
    )

    assert result.json()["attributes"] == {
        "email": "asha@example.com",
        "department": "Finance",
        "employee_number": "E-1001",
    }


async def test_field_mappings_for_unknown_flow_are_rejected(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    response = await client.put(
        "/v1/client/integrations/acme-identity",
        json={"field_mappings": {"no_such_flow": {"a": "b"}}},
        headers=setup["key"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_flows"


async def test_provider_failure_fails_the_session(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    provider.reply = lambda _: httpx.Response(502, text="<html>down</html>")

    started = await start(client, setup, id_number=AADHAAR)

    body = started.json()
    assert started.status_code == 201
    assert body["status"] == "failed"
    assert body["failure_code"] == "provider_error"
    result = await client.get(
        session_url(body["id"], "result"), headers=setup["key"]
    )
    assert result.status_code == 409


async def test_missing_capture_fails_with_unexpected_response(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    provider.reply = lambda _: httpx.Response(
        200, json={"success": True, "data": {}}
    )

    started = await start(client, setup, id_number=AADHAAR)

    assert started.json()["status"] == "failed"
    assert started.json()["failure_code"] == "unexpected_response"


async def test_each_step_is_audited(
    client: AsyncClient,
    setup: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await start(client, setup, id_number=AADHAAR, otp=OTP)

    async with session_factory() as session:
        logs = (await session.scalars(select(IntegrationOperationLog))).all()
    assert sorted(log.operation for log in logs) == [
        "generate_otp",
        "submit_otp",
    ]
    assert all(log.succeeded for log in logs)


async def test_unknown_flow_returns_404(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    response = await start(client, setup, flow="nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "flow_not_found"


async def test_inputs_the_flow_never_uses_are_rejected(
    client: AsyncClient, setup: dict[str, Any], provider: FakeProvider
) -> None:
    response = await start(client, setup, id_number=AADHAAR, pan="X")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unexpected_inputs"
    assert provider.requests == []


async def test_inputs_to_finished_session_conflict(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    started = await start(client, setup, id_number=AADHAAR, otp=OTP)

    response = await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=setup["key"],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_active"


async def test_cancel_discards_inputs(
    client: AsyncClient,
    setup: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    before = await secret_count(session_factory)
    started = await start(client, setup, id_number=AADHAAR)

    cancelled = await client.post(
        session_url(started.json()["id"], "cancel"), headers=setup["key"]
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert await secret_count(session_factory) == before
    again = await client.post(
        session_url(started.json()["id"], "cancel"), headers=setup["key"]
    )
    assert again.status_code == 409


async def test_overdue_session_expires_when_polled(
    client: AsyncClient,
    setup: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started = await start(client, setup, id_number=AADHAAR)
    async with session_factory() as session:
        stored = await session.get(
            IntegrationSession, uuid.UUID(started.json()["id"])
        )
        stored.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    polled = await client.get(
        session_url(started.json()["id"]), headers=setup["key"]
    )
    late = await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=setup["key"],
    )

    assert polled.json()["status"] == "expired"
    assert late.status_code == 409


async def test_sessions_are_private_to_their_client(
    client: AsyncClient,
    setup: dict[str, Any],
) -> None:
    started = await start(client, setup, id_number=AADHAAR)
    other = await create_client(client, setup["admin"], code="other-portal")
    other_key = await issue_api_key(client, setup["admin"], other["id"])

    response = await client.get(
        session_url(started.json()["id"]),
        headers={"X-API-Key": other_key["api_key"]},
    )

    assert response.status_code == 404


async def test_list_sessions_filters_by_status(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    await start(client, setup, id_number=AADHAAR)
    await start(client, setup, id_number=AADHAAR, otp=OTP)

    everything = await client.get(sessions_url(), headers=setup["key"])
    waiting = await client.get(
        sessions_url(),
        params={"status": "awaiting_input"},
        headers=setup["key"],
    )

    assert everything.json()["total"] == 2
    assert waiting.json()["total"] == 1
    assert waiting.json()["items"][0]["awaiting_inputs"] == ["otp"]


async def test_tool_listing_describes_flows(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/integration-tools", headers=setup["admin"]
    )

    (tool,) = response.json()["items"]
    flows = {flow["name"]: flow for flow in tool["connector"]["flows"]}
    assert flows["aadhaar_otp"]["inputs"] == ["id_number", "otp"]
    assert flows["aadhaar_otp"]["purpose"] == "verification"
    assert flows["employee_profile"]["outputs"] == ["email", "department"]


async def test_worker_delivers_signed_event_and_purges_result(
    client: AsyncClient,
    setup: dict[str, Any],
    provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    signing_secret = "whsec_test"
    async with session_factory() as session:
        reference = await _store_local_secret(
            session, {"secret": signing_secret}
        )
        session.add(
            ClientWebhook(
                client_id=uuid.UUID(setup["client_id"]),
                url="https://hooks.example.com/everycred",
                secret_reference=reference,
            )
        )
        await session.commit()

    started = await start(client, setup, id_number=AADHAAR, otp=OTP)
    session_id = started.json()["id"]
    settings = get_settings().model_copy(
        update={"webhook_allow_private_networks": True}
    )

    await run_once(session_factory, provider.http_client, settings)

    hook = provider.requests[-1]
    assert hook.url.host == "hooks.example.com"
    assert hook.headers["X-EveryCRED-Event"] == "session.completed"
    assert webhook_delivery.verify_signature(
        signing_secret, hook.headers["X-EveryCRED-Signature"], hook.content
    )
    event = json.loads(hook.content)
    assert event["data"]["session_id"] == session_id
    assert event["data"]["outcome"] == "verified"
    assert "Asha" not in hook.content.decode()
    async with session_factory() as session:
        delivery = await session.scalar(select(WebhookDelivery))
        assert delivery.status == DeliveryStatus.DELIVERED

        stored = await session.get(IntegrationSession, uuid.UUID(session_id))
        stored.data_expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    await run_once(session_factory, provider.http_client, settings)

    result = await client.get(
        session_url(session_id, "result"), headers=setup["key"]
    )
    assert result.status_code == 410


async def _store_local_secret(
    session: AsyncSession, value: dict[str, Any]
) -> str:
    from app.core.secret_store import build_secret_store

    store = build_secret_store(session, get_settings())
    return await store.create(
        f"test/{uuid.uuid4()}", value, tags={"purpose": "test"}
    )
