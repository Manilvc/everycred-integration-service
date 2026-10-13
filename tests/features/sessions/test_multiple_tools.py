import copy
from typing import Any

import pytest
from httpx import AsyncClient

from tests.features.sessions.conftest import (
    AADHAAR,
    API_TOKEN,
    OTP,
    TOOL_DEFINITION,
    USER,
    FakeProvider,
    session_url,
    start,
)

# A second provider offering the same flows, with its own environment
# names, as SurePass and Microsoft Entra ID differ in practice.
BETA_DEFINITION: dict[str, Any] = copy.deepcopy(TOOL_DEFINITION)
BETA_DEFINITION["name"] = "Beta Identity"
BETA_DEFINITION["connector_config"]["environments"] = {
    "test": "http://127.0.0.1:9/beta-test",
    "live": "http://127.0.0.1:9/beta-live",
}
BETA_DEFINITION["connector_config"]["default_environment"] = "test"


def configs_url(setup: dict[str, Any], code: str = "") -> str:
    base = f"/v1/clients/{setup['client_id']}/integrations"
    return f"{base}/{code}" if code else base


async def route(
    client: AsyncClient, setup: dict[str, Any], code: str, **body: Any
) -> dict[str, Any]:
    response = await client.put(
        configs_url(setup, code), json=body, headers=setup["admin"]
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def two_tools(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    """Acme stays the default for both types; Beta is added on live."""
    saved = await client.put(
        "/v1/integration-tools/beta-identity",
        json=BETA_DEFINITION,
        headers=setup["admin"],
    )
    assert saved.status_code == 200, saved.text
    credentials = await client.put(
        f"/v1/clients/{setup['client_id']}/tools/beta-identity/credentials",
        json={"credentials": {"api_token": API_TOKEN}},
        headers=setup["admin"],
    )
    assert credentials.status_code == 200, credentials.text
    for code in ("confirm", "gather"):
        await route(
            client,
            setup,
            code,
            tool_code="acme-identity",
            settings={"environment": "sandbox"},
        )
        await route(
            client,
            setup,
            code,
            tool_code="beta-identity",
            is_default=False,
            settings={"environment": "live"},
        )
    return setup


async def test_each_tool_keeps_its_own_settings(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    config = await route(
        client,
        two_tools,
        "confirm",
        tool_code="beta-identity",
        is_default=False,
        settings={"environment": "test"},
    )

    tools = {item["tool"]["code"]: item for item in config["tools"]}
    assert tools["acme-identity"]["settings"] == {"environment": "sandbox"}
    assert tools["acme-identity"]["is_default"] is True
    assert tools["beta-identity"]["settings"] == {"environment": "test"}
    assert tools["beta-identity"]["is_default"] is False
    assert config["integration_tool"]["code"] == "acme-identity"
    assert config["settings"] == {"environment": "sandbox"}


async def test_session_runs_through_the_named_tool_on_its_environment(
    client: AsyncClient, two_tools: dict[str, Any], provider: FakeProvider
) -> None:
    named = await start(
        client,
        two_tools,
        flow="employee_profile",
        integration_type="gather",
        body={"tool_code": "beta-identity"},
        employee_id="E-1001",
    )
    default = await start(
        client,
        two_tools,
        flow="employee_profile",
        integration_type="gather",
        employee_id="E-1001",
    )

    assert named.status_code == 201, named.text
    assert named.json()["tool_code"] == "beta-identity"
    assert named.json()["outcome"] == "gathered"
    assert default.json()["tool_code"] == "acme-identity"
    assert provider.paths() == [
        "/beta-live/employees/E-1001",
        "/api/employees/E-1001",
    ]


async def test_open_session_keeps_its_tool_when_the_default_changes(
    client: AsyncClient, two_tools: dict[str, Any], provider: FakeProvider
) -> None:
    started = await start(client, two_tools, id_number=AADHAAR)
    await route(
        client,
        two_tools,
        "confirm",
        tool_code="beta-identity",
        settings={"environment": "live"},
    )

    finished = await client.post(
        session_url(started.json()["id"], "inputs"),
        json={"inputs": {"otp": OTP}},
        headers=two_tools["key"],
    )

    assert finished.json()["status"] == "completed", finished.text
    assert finished.json()["tool_code"] == "acme-identity"
    assert provider.paths() == ["/api/otp/generate", "/api/otp/submit"]


async def test_flows_of_a_named_tool(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/client/integration-types/confirm/flows",
        params={"tool_code": "beta-identity"},
        headers=two_tools["key"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["tool_code"] == "beta-identity"


async def test_tool_outside_the_type_is_not_found(
    client: AsyncClient,
    two_tools: dict[str, Any],
    super_admin_headers: dict[str, str],
) -> None:
    other = copy.deepcopy(BETA_DEFINITION)
    other["integration_types"] = ["gather"]
    await client.put(
        "/v1/integration-tools/hr-only",
        json=other,
        headers=super_admin_headers,
    )

    response = await start(
        client,
        two_tools,
        body={"tool_code": "hr-only"},
        id_number=AADHAAR,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"


async def test_switched_off_tool_cannot_be_named(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    switched = await client.put(
        "/v1/client/integrations/beta-identity",
        json={"is_enabled": False},
        headers=two_tools["key"],
    )
    assert switched.status_code == 200, switched.text

    response = await start(
        client,
        two_tools,
        body={"tool_code": "beta-identity"},
        id_number=AADHAAR,
    )

    assert response.status_code == 409


async def test_drawer_settings_change_only_that_tool(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    drawer = await client.put(
        "/v1/client/integrations/beta-identity",
        json={"settings": {"environment": "test"}},
        headers=two_tools["key"],
    )
    configs = await client.get(
        configs_url(two_tools), headers=two_tools["admin"]
    )

    assert drawer.status_code == 200, drawer.text
    assert drawer.json()["settings"] == {"environment": "test"}
    confirm = next(
        item
        for item in configs.json()
        if item["integration_type_code"] == "confirm"
    )
    settings = {
        item["tool"]["code"]: item["settings"] for item in confirm["tools"]
    }
    assert settings == {
        "acme-identity": {"environment": "sandbox"},
        "beta-identity": {"environment": "test"},
    }


async def test_user_listing_shows_every_usable_tool(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    response = await client.get(
        f"/v1/client/users/{USER}/integrations",
        params={"status": ["not_connected", "connected"]},
        headers=two_tools["key"],
    )

    assert response.status_code == 200, response.text
    groups = {
        group["key"]: group for group in response.json()["data"]["groups"]
    }
    systems = {
        system["code"]: system for system in groups["confirm"]["systems"]
    }
    assert set(systems) == {"acme-identity", "beta-identity"}
    assert systems["acme-identity"]["is_default"] is True
    assert systems["beta-identity"]["is_default"] is False
    assert systems["beta-identity"]["is_active"] is True
