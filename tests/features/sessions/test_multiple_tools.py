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


def connection_url(code: str = "confirm", action: str = "") -> str:
    base = f"/v1/client/users/{USER}/connections/{code}"
    return f"{base}/{action}" if action else base


async def save_connection(
    client: AsyncClient,
    setup: dict[str, Any],
    tool_code: str | None = None,
    **kwargs: Any,
) -> Any:
    return await client.put(
        connection_url(),
        params={"tool_code": tool_code} if tool_code else None,
        json={"kwargs": kwargs},
        headers=setup["key"],
    )


async def test_user_connects_to_each_tool_separately(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    default = await save_connection(client, two_tools, id_number=AADHAAR)
    beta = await save_connection(
        client, two_tools, "beta-identity", id_number=AADHAAR
    )

    assert default.status_code == 200, default.text
    assert beta.status_code == 200, beta.text
    assert default.json()["id"] != beta.json()["id"]
    assert default.json()["integration_tool"]["code"] == "acme-identity"
    assert beta.json()["integration_tool"]["code"] == "beta-identity"
    # Each connection ran on its own tool's environment.
    assert default.json()["connection_details"] == {"environment": "sandbox"}
    assert beta.json()["connection_details"] == {"environment": "live"}

    listed = await client.get(
        f"/v1/client/users/{USER}/connections", headers=two_tools["key"]
    )
    tools = [item["integration_tool"]["code"] for item in listed.json()]
    assert sorted(tools) == ["acme-identity", "beta-identity"]


async def test_connection_to_one_tool_is_read_and_deleted_alone(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    await save_connection(client, two_tools, id_number=AADHAAR)
    await save_connection(
        client, two_tools, "beta-identity", id_number=AADHAAR
    )
    beta = {"tool_code": "beta-identity"}

    read = await client.get(
        connection_url(), params=beta, headers=two_tools["key"]
    )
    deleted = await client.delete(
        connection_url(), params=beta, headers=two_tools["key"]
    )
    gone = await client.get(
        connection_url(), params=beta, headers=two_tools["key"]
    )
    kept = await client.get(connection_url(), headers=two_tools["key"])

    assert read.json()["integration_tool"]["code"] == "beta-identity"
    assert deleted.status_code == 204
    assert gone.status_code == 404
    assert kept.json()["integration_tool"]["code"] == "acme-identity"


async def test_reconnect_uses_the_named_tool(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    await save_connection(
        client, two_tools, "beta-identity", id_number=AADHAAR
    )

    without_default = await client.post(
        connection_url(action="connect"), headers=two_tools["key"]
    )
    named = await client.post(
        connection_url(action="connect"),
        params={"tool_code": "beta-identity"},
        headers=two_tools["key"],
    )

    # The default tool has no saved inputs for this user yet.
    assert without_default.status_code == 404
    assert named.status_code == 200, named.text
    assert named.json()["status"] == "connected"


async def test_user_listing_shows_each_tools_connection(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    await save_connection(
        client, two_tools, "beta-identity", id_number=AADHAAR
    )

    response = await client.get(
        f"/v1/client/users/{USER}/integrations",
        headers=two_tools["key"],
    )

    groups = {
        group["key"]: group for group in response.json()["data"]["groups"]
    }
    assert [system["code"] for system in groups["confirm"]["systems"]] == [
        "beta-identity"
    ]


async def test_connection_to_a_tool_outside_the_type_is_not_found(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    response = await save_connection(
        client, two_tools, "no-such-tool", id_number=AADHAAR
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "integration_tool_not_found"


async def test_inputs_saved_without_a_tool_go_to_the_next_default(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    await route(client, two_tools, "confirm")
    pending = await save_connection(client, two_tools, id_number=AADHAAR)
    await route(client, two_tools, "confirm", tool_code="beta-identity")

    connected = await client.post(
        connection_url(action="connect"), headers=two_tools["key"]
    )

    assert pending.json()["status"] == "pending"
    assert pending.json()["integration_tool"] is None
    assert connected.status_code == 200, connected.text
    assert connected.json()["id"] == pending.json()["id"]
    assert connected.json()["integration_tool"]["code"] == "beta-identity"


def tool_connection_url(code: str, action: str = "") -> str:
    base = f"/v1/client/users/{USER}/connections/{code}"
    return f"{base}/{action}" if action else base


async def test_connection_by_tool_code_in_the_path(
    client: AsyncClient,
    two_tools: dict[str, Any],
    super_admin_headers: dict[str, str],
) -> None:
    hr_only = copy.deepcopy(BETA_DEFINITION)
    hr_only["integration_types"] = ["gather"]
    await client.put(
        "/v1/integration-tools/hr-only",
        json=hr_only,
        headers=super_admin_headers,
    )
    await client.put(
        f"/v1/clients/{two_tools['client_id']}/tools/hr-only/credentials",
        json={"credentials": {"api_token": API_TOKEN}},
        headers=super_admin_headers,
    )

    saved = await client.put(
        tool_connection_url("hr-only"),
        json={"kwargs": {"employee_id": "E-1001"}},
        headers=two_tools["key"],
    )
    read = await client.get(
        tool_connection_url("hr-only"), headers=two_tools["key"]
    )

    # A tool serving one type needs nothing else in the URL.
    assert saved.status_code == 200, saved.text
    assert saved.json()["integration_type_code"] == "gather"
    assert saved.json()["integration_tool"]["code"] == "hr-only"
    assert saved.json()["status"] == "connected"
    assert read.json()["id"] == saved.json()["id"]


async def test_tool_serving_several_types_needs_the_type(
    client: AsyncClient, two_tools: dict[str, Any]
) -> None:
    ambiguous = await client.put(
        tool_connection_url("beta-identity"),
        json={"kwargs": {"id_number": AADHAAR}},
        headers=two_tools["key"],
    )
    named = await client.put(
        tool_connection_url("beta-identity"),
        params={"integration_type": "confirm"},
        json={"kwargs": {"id_number": AADHAAR}},
        headers=two_tools["key"],
    )

    assert ambiguous.status_code == 422
    assert ambiguous.json()["error"]["code"] == "integration_type_required"
    assert named.status_code == 200, named.text
    assert named.json()["integration_type_code"] == "confirm"
    assert named.json()["integration_tool"]["code"] == "beta-identity"


async def test_connection_by_tool_id_in_the_path(
    client: AsyncClient,
    two_tools: dict[str, Any],
    super_admin_headers: dict[str, str],
) -> None:
    tools = await client.get(
        "/v1/integration-tools", headers=super_admin_headers
    )
    beta_id = next(
        tool["id"]
        for tool in tools.json()["items"]
        if tool["code"] == "beta-identity"
    )
    await client.put(
        tool_connection_url("beta-identity"),
        params={"integration_type": "confirm"},
        json={"kwargs": {"id_number": AADHAAR}},
        headers=two_tools["key"],
    )

    response = await client.post(
        tool_connection_url(beta_id, "connect"),
        params={"integration_type": "confirm"},
        headers=two_tools["key"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["integration_tool"]["code"] == "beta-identity"


@pytest.mark.parametrize(
    ("code", "params", "error_code"),
    [
        ("no-such-tool", {}, "integration_tool_not_found"),
        (
            "beta-identity",
            {"integration_type": "confirm", "tool_code": "acme-identity"},
            "conflicting_tool_code",
        ),
        (
            "beta-identity",
            {"integration_type": "records"},
            "integration_tool_not_found",
        ),
    ],
)
async def test_tool_path_rejects_bad_requests(
    client: AsyncClient,
    two_tools: dict[str, Any],
    code: str,
    params: dict[str, str],
    error_code: str,
) -> None:
    response = await client.put(
        tool_connection_url(code),
        params=params,
        json={"kwargs": {"id_number": AADHAAR}},
        headers=two_tools["key"],
    )

    assert response.json()["error"]["code"] == error_code, response.text
