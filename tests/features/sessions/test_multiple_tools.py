"""Sessions on a tool other than the one chosen for the type."""

import copy
from typing import Any

import pytest
from httpx import AsyncClient

from tests.features.sessions.conftest import (
    AADHAAR,
    API_TOKEN,
    OTP,
    TOOL_DEFINITION,
    FakeProvider,
    session_url,
    start,
)

SECOND_TOOL = "beta-identity"
GATHER_ONLY_TOOL = "hr-directory"


def tool_definition(name: str, integration_types: list[str]) -> dict:
    definition = copy.deepcopy(TOOL_DEFINITION)
    definition["name"] = name
    definition["integration_types"] = integration_types
    return definition


@pytest.fixture
async def tools(client: AsyncClient, setup: dict[str, Any]) -> dict[str, Any]:
    """Add a second Confirm tool and a Gather-only tool, unchosen."""
    for code, name, types in (
        (SECOND_TOOL, "Beta Identity", ["confirm"]),
        (GATHER_ONLY_TOOL, "HR Directory", ["gather"]),
    ):
        tool = await client.put(
            f"/v1/integration-tools/{code}",
            json=tool_definition(name, types),
            headers=setup["admin"],
        )
        assert tool.status_code == 200, tool.text
        credentials = await client.put(
            f"/v1/clients/{setup['client_id']}/tools/{code}/credentials",
            json={"credentials": {"api_token": API_TOKEN}},
            headers=setup["admin"],
        )
        assert credentials.status_code == 200, credentials.text
    return setup


async def test_flows_catalog_describes_the_named_tool(
    client: AsyncClient, tools: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/client/integration-types/confirm/flows",
        params={"tool_code": SECOND_TOOL},
        headers=tools["key"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["tool_code"] == SECOND_TOOL


async def test_flows_catalog_defaults_to_the_chosen_tool(
    client: AsyncClient, tools: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/client/integration-types/confirm/flows", headers=tools["key"]
    )

    assert response.status_code == 200, response.text
    assert response.json()["tool_code"] == "acme-identity"


async def test_session_runs_on_the_named_tool_to_completion(
    client: AsyncClient, tools: dict[str, Any], provider: FakeProvider
) -> None:
    started = await start(
        client, tools, body={"tool_code": SECOND_TOOL}, id_number=AADHAAR
    )

    assert started.status_code == 201, started.text
    session = started.json()
    assert session["tool_code"] == SECOND_TOOL
    assert session["status"] == "awaiting_input"

    finished = await client.post(
        session_url(session["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=tools["key"],
    )

    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "completed"
    assert finished.json()["outcome"] == "verified"


async def test_tool_that_does_not_serve_the_type_is_not_found(
    client: AsyncClient, tools: dict[str, Any], provider: FakeProvider
) -> None:
    response = await start(
        client,
        tools,
        body={"tool_code": GATHER_ONLY_TOOL},
        id_number=AADHAAR,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"
    assert provider.requests == []


async def test_unknown_tool_is_not_found(
    client: AsyncClient, tools: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/client/integration-types/confirm/flows",
        params={"tool_code": "no-such-tool"},
        headers=tools["key"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"


async def test_tool_the_client_switched_off_is_refused(
    client: AsyncClient, tools: dict[str, Any], provider: FakeProvider
) -> None:
    switched_off = await client.put(
        f"/v1/client/integrations/{SECOND_TOOL}",
        json={"is_enabled": False},
        headers=tools["key"],
    )
    assert switched_off.status_code == 200, switched_off.text

    response = await start(
        client, tools, body={"tool_code": SECOND_TOOL}, id_number=AADHAAR
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "integration_tool_disabled"
    assert provider.requests == []


async def test_malformed_tool_code_is_rejected(
    client: AsyncClient, tools: dict[str, Any]
) -> None:
    response = await start(
        client, tools, body={"tool_code": "Bad Code!"}, id_number=AADHAAR
    )

    assert response.status_code == 422
