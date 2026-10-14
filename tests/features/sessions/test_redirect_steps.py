"""Flows that send the holder to a provider page from a step, then resume."""

import copy
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient

from tests.features.sessions.conftest import (
    TOOL_DEFINITION,
    FakeProvider,
    default_reply,
    session_url,
    start,
)
from tests.features.sessions.test_sign_in_flows import (
    CALLBACK_URL,
    REDIRECT_URI,
    TOOL_URL,
)

FLOW = "provider_consent"
CONSENT_PAGE = "https://consent.example.com/approve?ref=c-1"

CONSENT_FLOW: dict[str, Any] = {
    "purpose": "verification",
    "description": "Approve sharing on the provider's page",
    "steps": [
        {
            "operation": "start_consent",
            "capture": {"client_ref": "client_id"},
            "redirect": {"url": "url"},
        },
        {"operation": "fetch_record"},
    ],
    "outputs": {"full_name": "record.full_name"},
    "verified_when": [{"path": "record.full_name"}],
}


def tool_definition(flow: dict[str, Any]) -> dict[str, Any]:
    definition = copy.deepcopy(TOOL_DEFINITION)
    operations = definition["connector_config"]["operations"]
    operations["start_consent"] = {
        "path": "/consent/start",
        "body": {"data": {"redirect_url": "{session.return_url}"}},
    }
    operations["fetch_record"] = {
        "method": "GET",
        "path": "/consent/{session.client_ref}/record",
    }
    definition["connector_config"]["flows"][FLOW] = flow
    return definition


@pytest.fixture
async def consent(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    saved = await client.put(
        TOOL_URL, json=tool_definition(CONSENT_FLOW), headers=setup["admin"]
    )
    assert saved.status_code == 200, saved.text
    config = await client.put(
        f"/v1/clients/{setup['client_id']}/integrations/confirm",
        json={
            "tool_code": "acme-identity",
            "settings": {"redirect_uris": [REDIRECT_URI]},
        },
        headers=setup["admin"],
    )
    assert config.status_code == 200, config.text
    return setup


def answer_as_provider(
    provider: FakeProvider, page: str = CONSENT_PAGE
) -> list[httpx.Request]:
    """Answer the consent start and record calls; return the starts."""
    starts: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/consent/start"):
            starts.append(request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"client_id": "c-1", "url": page},
                },
            )
        if path.endswith("/consent/c-1/record"):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"record": {"full_name": "Asha Verma"}},
                },
            )
        return default_reply(request)

    provider.reply = reply
    return starts


async def start_consent(
    client: AsyncClient, setup: dict[str, Any]
) -> httpx.Response:
    return await start(
        client, setup, flow=FLOW, body={"redirect_uri": REDIRECT_URI}
    )


def return_state(start_request: httpx.Request) -> str:
    """The state the provider was told to send the holder back with."""
    body = json.loads(start_request.content)
    query = parse_qs(urlsplit(body["data"]["redirect_url"]).query)
    return query["state"][0]


async def test_flow_catalog_describes_a_redirect(
    client: AsyncClient, consent: dict[str, Any]
) -> None:
    response = await client.get(
        "/v1/client/integration-types/confirm/flows", headers=consent["key"]
    )

    flows = {flow["name"]: flow for flow in response.json()["flows"]}
    assert flows[FLOW]["kind"] == "redirect"
    assert flows[FLOW]["inputs"] == []


async def test_start_sends_the_holder_to_the_provider_page(
    client: AsyncClient, consent: dict[str, Any], provider: FakeProvider
) -> None:
    starts = answer_as_provider(provider)

    started = await start_consent(client, consent)

    assert started.status_code == 201, started.text
    session = started.json()
    assert session["status"] == "awaiting_redirect"
    assert session["next_action"] == {"type": "redirect", "url": CONSENT_PAGE}
    (start_request,) = starts
    sent = urlsplit(json.loads(start_request.content)["data"]["redirect_url"])
    assert f"{sent.scheme}://{sent.netloc}{sent.path}" == REDIRECT_URI
    assert len(return_state(start_request)) >= 32


async def test_return_with_state_runs_the_remaining_steps(
    client: AsyncClient, consent: dict[str, Any], provider: FakeProvider
) -> None:
    starts = answer_as_provider(provider)
    started = (await start_consent(client, consent)).json()

    finished = await client.post(
        CALLBACK_URL,
        json={"state": return_state(starts[0])},
        headers=consent["key"],
    )

    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["id"] == started["id"]
    assert body["status"] == "completed"
    assert body["outcome"] == "verified"
    assert provider.paths()[-1] == "/api/consent/c-1/record"
    result = await client.get(
        session_url(body["id"], "result"), headers=consent["key"]
    )
    assert result.json()["attributes"] == {"full_name": "Asha Verma"}


async def test_return_state_works_only_once(
    client: AsyncClient, consent: dict[str, Any], provider: FakeProvider
) -> None:
    starts = answer_as_provider(provider)
    await start_consent(client, consent)
    callback = {"state": return_state(starts[0])}

    first = await client.post(
        CALLBACK_URL, json=callback, headers=consent["key"]
    )
    replay = await client.post(
        CALLBACK_URL, json=callback, headers=consent["key"]
    )

    assert first.status_code == 200
    assert replay.status_code == 404
    assert replay.json()["error"]["code"] == "authorization_not_found"


async def test_return_with_error_fails_without_fetching(
    client: AsyncClient, consent: dict[str, Any], provider: FakeProvider
) -> None:
    starts = answer_as_provider(provider)
    await start_consent(client, consent)

    declined = await client.post(
        CALLBACK_URL,
        json={"state": return_state(starts[0]), "error": "access_denied"},
        headers=consent["key"],
    )

    assert declined.json()["status"] == "failed"
    assert declined.json()["failure_code"] == "authorization_declined"
    assert "/api/consent/c-1/record" not in provider.paths()


@pytest.mark.parametrize(
    "page", ["http://consent.example.com/approve", "javascript:alert(1)"]
)
async def test_unsafe_provider_page_fails_the_session(
    client: AsyncClient,
    consent: dict[str, Any],
    provider: FakeProvider,
    page: str,
) -> None:
    answer_as_provider(provider, page=page)

    started = await start_consent(client, consent)

    session = started.json()
    assert session["status"] == "failed"
    assert session["failure_code"] == "unexpected_response"
    assert session["next_action"] is None


async def test_redirect_uri_is_required(
    client: AsyncClient, consent: dict[str, Any], provider: FakeProvider
) -> None:
    response = await start(client, consent, flow=FLOW)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_redirect_uri"
    assert provider.requests == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"steps": [CONSENT_FLOW["steps"][0]]},
            "a redirect step needs a step after it",
        ),
        (
            {
                "steps": [
                    CONSENT_FLOW["steps"][0],
                    {"operation": "fetch_employee", "inputs": ["employee_id"]},
                ]
            },
            "flows with a redirect step may not take inputs",
        ),
        (
            {
                "steps": [
                    {
                        **CONSENT_FLOW["steps"][0],
                        "capture": {"return_url": "x"},
                    },
                    CONSENT_FLOW["steps"][1],
                ]
            },
            "capture name 'return_url' is reserved",
        ),
        (
            {
                "steps": [
                    CONSENT_FLOW["steps"][0],
                    {**CONSENT_FLOW["steps"][1], "redirect": {"url": "url"}},
                ]
            },
            "a flow may send the holder away only once",
        ),
    ],
)
async def test_redirect_step_configuration_is_checked(
    client: AsyncClient,
    setup: dict[str, Any],
    change: dict[str, Any],
    message: str,
) -> None:
    response = await client.put(
        TOOL_URL,
        json=tool_definition({**CONSENT_FLOW, **change}),
        headers=setup["admin"],
    )

    assert response.status_code == 422
    assert message in response.text


async def test_return_url_is_not_available_to_other_flows(
    client: AsyncClient, setup: dict[str, Any]
) -> None:
    flow = copy.deepcopy(CONSENT_FLOW)
    del flow["steps"][0]["redirect"]

    response = await client.put(
        TOOL_URL, json=tool_definition(flow), headers=setup["admin"]
    )

    assert response.status_code == 422
    assert "return_url" in response.text
