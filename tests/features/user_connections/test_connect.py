import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.integration_tools.models import IntegrationTool
from tests.features.user_connections.conftest import (
    USER_UUID,
    connection_url,
    recorded_calls,
)


async def save_and_connect(
    client: AsyncClient,
    headers: dict[str, str],
    code: str,
    parameters: dict,
):
    await client.put(connection_url(code), json=parameters, headers=headers)
    return await client.post(
        f"{connection_url(code)}/connect", headers=headers
    )


async def test_connect_calls_connector_with_args_and_kwargs(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client,
        client_headers,
        "confirm",
        {"args": ["tok-123"], "kwargs": {"region": "eu"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["connection_details"] == {"account_ref": "acct-eu"}
    assert body["last_connected_at"] is not None
    call = recorded_calls[0]
    assert (call.api_token, call.region) == ("tok-123", "eu")
    assert str(call.context.user_uuid) == USER_UUID
    assert call.context.client_settings == {"environment": "sandbox"}


async def test_connector_defaults_apply_when_kwargs_omitted(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    await save_and_connect(
        client, client_headers, "confirm", {"kwargs": {"api_token": "t"}}
    )

    assert recorded_calls[0].region == "in"


async def test_rejected_connection_is_saved_as_failed(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "gather", {"args": ["bad-token"]}
    )
    stored = await client.get(connection_url("gather"), headers=client_headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "connection_failed"
    assert stored.json()["status"] == "failed"
    assert stored.json()["last_error"] == (
        "The provider rejected the credentials."
    )


async def test_connector_crash_does_not_leak_details(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "enforcement", {"args": ["tok-secret"]}
    )
    stored = await client.get(
        connection_url("enforcement"), headers=client_headers
    )

    assert response.status_code == 502
    assert "tok-secret" not in response.text
    assert "tok-secret" not in stored.text
    assert stored.json()["last_error"] == (
        "The integration failed unexpectedly."
    )


async def test_connector_crash_keeps_secrets_out_of_logs(
    client: AsyncClient,
    client_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    await save_and_connect(
        client, client_headers, "enforcement", {"args": ["tok-secret"]}
    )

    assert "RuntimeError" in caplog.text
    assert "tok-secret" not in caplog.text


async def test_hanging_connector_times_out(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "records", {"args": ["t"]}
    )

    assert response.status_code == 502
    assert "respond in time" in response.json()["error"]["message"]


async def test_connect_without_connector_returns_501(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "unwired", {"args": []}
    )

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "connector_not_available"


async def test_connect_before_saving_parameters_returns_404(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"{connection_url('confirm')}/connect", headers=client_headers
    )

    assert response.status_code == 404


async def test_saving_parameters_connects_with_them(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("confirm"),
        json={"args": ["t"], "kwargs": {"region": "eu"}},
        headers=client_headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "connected"
    assert body["connection_details"] == {"account_ref": "acct-eu"}
    assert body["last_connected_at"] is not None
    assert [(c.api_token, c.region) for c in recorded_calls] == [("t", "eu")]


async def test_saving_new_parameters_reconnects_with_them(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    await client.put(
        connection_url("confirm"), json={"args": ["t"]}, headers=client_headers
    )

    response = await client.put(
        connection_url("confirm"),
        json={"args": ["t2"]},
        headers=client_headers,
    )

    assert response.json()["status"] == "connected"
    assert recorded_calls[-1].api_token == "t2"


async def test_saving_with_a_rejecting_tool_is_saved_as_failed(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("gather"),
        json={"args": ["bad-token"]},
        headers=client_headers,
    )

    # The save succeeds; the failed attempt is reported, not raised.
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["last_error"] == (
        "The provider rejected the credentials."
    )


async def test_saving_without_a_connector_stays_pending(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await client.put(
        connection_url("unwired"), json={"args": []}, headers=client_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["last_attempt_at"] is None


async def test_connector_context_names_type_and_tool(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "confirm", {"args": ["t"]}
    )

    context = recorded_calls[0].context
    assert context.integration_type_code == "confirm"
    assert context.tool_code == "recording-tool"
    assert response.json()["integration_tool"]["code"] == "recording-tool"


async def test_connect_without_chosen_tool_returns_409(
    client: AsyncClient, client_headers: dict[str, str]
) -> None:
    response = await save_and_connect(
        client, client_headers, "no-tool", {"args": ["t"]}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "integration_tool_not_selected"


async def test_connect_with_deactivated_tool_returns_409(
    client: AsyncClient,
    client_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.put(
        connection_url("confirm"), json={"args": ["t"]}, headers=client_headers
    )
    # Saving connected once; only calls after deactivation matter here.
    recorded_calls.clear()
    async with session_factory() as session:
        await session.execute(
            update(IntegrationTool)
            .where(IntegrationTool.code == "recording-tool")
            .values(is_active=False)
        )
        await session.commit()

    response = await client.post(
        f"{connection_url('confirm')}/connect", headers=client_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "integration_tool_inactive"
    assert recorded_calls == []
