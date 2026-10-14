import base64
import copy
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.user_connections.models import IntegrationOperationLog
from tests.features.sessions.conftest import (
    API_TOKEN,
    TOOL_DEFINITION,
    FakeProvider,
    default_reply,
    session_url,
    start,
)

TOOL_URL = "/v1/integration-tools/acme-identity"
REDIRECT_URI = "https://backend.example.com/v1/holder/kyc/callback"
CLIENT_ID = "app-client-id"
CLIENT_SECRET = "app-client-secret-do-not-leak"
TENANT = "tenant-1"
ISSUER = f"https://login.example.com/{TENANT}/v2.0"
CALLBACK_URL = "/v1/client/sessions/authorization-callback"

SIGN_IN_FLOW: dict[str, Any] = {
    "purpose": "verification",
    "description": "Sign in with the organisation's directory",
    "authorization": {
        "authorize_url": "http://127.0.0.1:9/{credentials.tenant_id}/authorize",
        "token_url": "http://127.0.0.1:9/{credentials.tenant_id}/token",
        "issuer": "https://login.example.com/{credentials.tenant_id}/v2.0",
        "extra_params": {"prompt": "login"},
        "capture": {"subject": "oid"},
    },
    "outputs": {
        "full_name": "name",
        "email": "preferred_username",
        "directory_id": "session.subject",
    },
    "verified_when": [{"path": "tid", "equals": TENANT}],
}


def id_token(nonce: str, **overrides: Any) -> str:
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "nonce": nonce,
        "oid": "oid-123",
        "tid": TENANT,
        "name": "Asha Verma",
        "preferred_username": "asha@example.com",
        **overrides,
    }
    # The signature is not checked for tokens received directly from
    # the token endpoint, so any key will do here.
    return jwt.encode(claims, "test-signing-key-32-bytes-long!!", "HS256")


@pytest.fixture
async def sign_in(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    definition = copy.deepcopy(TOOL_DEFINITION)
    definition["connector_config"]["flows"]["directory_sign_in"] = SIGN_IN_FLOW
    saved = await client.put(TOOL_URL, json=definition, headers=setup["admin"])
    assert saved.status_code == 200, saved.text
    credentials = await client.put(
        f"/v1/clients/{setup['client_id']}/tools/acme-identity/credentials",
        json={
            "credentials": {
                "api_token": API_TOKEN,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "tenant_id": TENANT,
            }
        },
        headers=setup["admin"],
    )
    assert credentials.status_code == 200, credentials.text
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


def answer_token_requests(
    provider: FakeProvider, make_token: Any
) -> list[httpx.Request]:
    token_requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            token_requests.append(request)
            form = parse_qs(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-not-kept",
                    "id_token": make_token(form),
                },
            )
        return default_reply(request)

    provider.reply = reply
    return token_requests


async def start_sign_in(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    started = await start(
        client,
        setup,
        flow="directory_sign_in",
        body={"redirect_uri": REDIRECT_URI},
    )
    assert started.status_code == 201, started.text
    return started.json()


def query_of(url: str) -> dict[str, str]:
    return {
        name: values[0]
        for name, values in parse_qs(urlsplit(url).query).items()
    }


async def test_sign_in_flow_returns_authorization_url(
    client: AsyncClient, sign_in: dict[str, Any], provider: FakeProvider
) -> None:
    session = await start_sign_in(client, sign_in)

    assert session["status"] == "awaiting_redirect"
    assert session["next_action"]["type"] == "redirect"
    url = session["next_action"]["url"]
    assert url.startswith(f"http://127.0.0.1:9/{TENANT}/authorize?")
    query = query_of(url)
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert query["prompt"] == "login"
    assert "openid" in query["scope"].split()
    assert CLIENT_SECRET not in url
    assert provider.requests == []

    polled = await client.get(
        session_url(session["id"]), headers=sign_in["key"]
    )
    assert polled.json()["next_action"] is None


async def test_callback_exchanges_code_and_verifies_from_claims(
    client: AsyncClient,
    sign_in: dict[str, Any],
    provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session = await start_sign_in(client, sign_in)
    query = query_of(session["next_action"]["url"])
    token_requests = answer_token_requests(
        provider, lambda form: id_token(query["nonce"])
    )

    finished = await client.post(
        CALLBACK_URL,
        json={"state": query["state"], "code": "auth-code-1"},
        headers=sign_in["key"],
    )

    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "completed"
    assert body["outcome"] == "verified"
    assert body["result_attributes"] == ["directory_id", "email", "full_name"]
    form = parse_qs(token_requests[0].content.decode())
    verifier = form["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == query["code_challenge"]
    assert form["code"] == ["auth-code-1"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_secret"] == [CLIENT_SECRET]

    result = await client.get(
        session_url(body["id"], "result"), headers=sign_in["key"]
    )
    assert result.json()["attributes"] == {
        "full_name": "Asha Verma",
        "email": "asha@example.com",
        "directory_id": "oid-123",
    }
    assert "access-token-not-kept" not in result.text
    async with session_factory() as db:
        log = await db.scalar(select(IntegrationOperationLog))
        assert log is not None
        assert log.operation == "authorization_code_exchange"
        assert log.succeeded is True


async def test_state_works_only_once(
    client: AsyncClient, sign_in: dict[str, Any], provider: FakeProvider
) -> None:
    session = await start_sign_in(client, sign_in)
    query = query_of(session["next_action"]["url"])
    answer_token_requests(provider, lambda form: id_token(query["nonce"]))
    callback = {"state": query["state"], "code": "auth-code-1"}

    first = await client.post(
        CALLBACK_URL, json=callback, headers=sign_in["key"]
    )
    replay = await client.post(
        CALLBACK_URL, json=callback, headers=sign_in["key"]
    )

    assert first.status_code == 200
    assert replay.status_code == 404
    assert replay.json()["error"]["code"] == "authorization_not_found"


async def test_provider_error_fails_the_session(
    client: AsyncClient, sign_in: dict[str, Any], provider: FakeProvider
) -> None:
    session = await start_sign_in(client, sign_in)
    query = query_of(session["next_action"]["url"])

    declined = await client.post(
        CALLBACK_URL,
        json={"state": query["state"], "error": "access_denied"},
        headers=sign_in["key"],
    )

    body = declined.json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "authorization_declined"
    assert provider.requests == []


async def test_id_token_with_wrong_nonce_is_rejected(
    client: AsyncClient, sign_in: dict[str, Any], provider: FakeProvider
) -> None:
    session = await start_sign_in(client, sign_in)
    query = query_of(session["next_action"]["url"])
    answer_token_requests(provider, lambda form: id_token("another-nonce"))

    finished = await client.post(
        CALLBACK_URL,
        json={"state": query["state"], "code": "auth-code-1"},
        headers=sign_in["key"],
    )

    body = finished.json()
    assert body["status"] == "failed"
    assert body["failure_code"] == "provider_error"
    assert body["result_attributes"] == []


async def test_other_tenant_is_not_verified(
    client: AsyncClient, sign_in: dict[str, Any], provider: FakeProvider
) -> None:
    session = await start_sign_in(client, sign_in)
    query = query_of(session["next_action"]["url"])
    answer_token_requests(
        provider, lambda form: id_token(query["nonce"], tid="tenant-2")
    )

    finished = await client.post(
        CALLBACK_URL,
        json={"state": query["state"], "code": "auth-code-1"},
        headers=sign_in["key"],
    )

    assert finished.json()["outcome"] == "not_verified"


async def test_redirect_uri_must_be_registered(
    client: AsyncClient, sign_in: dict[str, Any]
) -> None:
    missing = await start(client, sign_in, flow="directory_sign_in")
    unknown = await start(
        client,
        sign_in,
        flow="directory_sign_in",
        body={"redirect_uri": "https://attacker.example.net/callback"},
    )
    not_a_sign_in = await start(
        client, sign_in, body={"redirect_uri": REDIRECT_URI}
    )

    for response in (missing, unknown, not_a_sign_in):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_redirect_uri"


async def test_plain_http_redirect_uri_is_refused_even_if_registered(
    client: AsyncClient, sign_in: dict[str, Any]
) -> None:
    insecure = "http://backend.example.com/v1/holder/kyc/callback"
    await client.put(
        "/v1/client/integrations/confirm/settings",
        json={"settings": {"redirect_uris": [insecure]}},
        headers=sign_in["key"],
    )

    response = await start(
        client,
        sign_in,
        flow="directory_sign_in",
        body={"redirect_uri": insecure},
    )

    assert response.status_code == 422
    assert "https" in response.json()["error"]["message"]


async def test_inputs_are_refused_while_waiting_for_sign_in(
    client: AsyncClient, sign_in: dict[str, Any]
) -> None:
    session = await start_sign_in(client, sign_in)

    response = await client.post(
        session_url(session["id"], "inputs"),
        json={"inputs": {"otp": "123456"}},
        headers=sign_in["key"],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_awaiting_input"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "authorize_url": (
                    "https://login.example.com/{credentials.client_secret}/a"
                )
            },
            "client secret",
        ),
        ({"scope": "profile email"}, "openid"),
        ({"extra_params": {"state": "fixed"}}, "state"),
        ({"authorize_url": "https://{settings.host}/authorize"}, "host"),
    ],
)
async def test_unsafe_sign_in_configuration_is_rejected(
    client: AsyncClient,
    setup: dict[str, Any],
    change: dict[str, Any],
    message: str,
) -> None:
    definition = copy.deepcopy(TOOL_DEFINITION)
    flow = copy.deepcopy(SIGN_IN_FLOW)
    flow["authorization"].update(change)
    definition["connector_config"]["flows"]["directory_sign_in"] = flow

    response = await client.put(
        TOOL_URL, json=definition, headers=setup["admin"]
    )

    assert response.status_code == 422
    assert message in response.text
