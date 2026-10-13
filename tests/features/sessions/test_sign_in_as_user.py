"""Sign-in flows whose steps call the provider as the signed-in user."""

import copy
import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.encryption import decrypt_json
from app.core.secret_store import LocalSecret
from tests.features.sessions.conftest import (
    API_TOKEN,
    TOOL_DEFINITION,
    FakeProvider,
    default_reply,
    session_url,
    start,
)
from tests.features.sessions.test_sign_in_flows import (
    CALLBACK_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    SIGN_IN_FLOW,
    TENANT,
    TOOL_URL,
    id_token,
    query_of,
)

USER_ACCESS_TOKEN = "user-access-token-not-kept"
FLOW = "directory_profile"

PROFILE_FLOW: dict[str, Any] = {
    "purpose": "verification",
    "description": "Sign in, then read your own profile",
    "authorization": {
        **SIGN_IN_FLOW["authorization"],
        "scope": "openid profile email User.Read",
        "steps_as_user": True,
    },
    "steps": [{"operation": "fetch_me"}],
    "outputs": {"full_name": "displayName", "department": "department"},
    "verified_when": [{"path": "id"}],
}


def tool_definition(flow: dict[str, Any]) -> dict[str, Any]:
    definition = copy.deepcopy(TOOL_DEFINITION)
    definition["connector_config"]["operations"]["fetch_me"] = {
        "method": "GET",
        "path": "/me",
    }
    definition["connector_config"]["flows"][FLOW] = flow
    return definition


@pytest.fixture
async def profile(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    saved = await client.put(
        TOOL_URL, json=tool_definition(PROFILE_FLOW), headers=setup["admin"]
    )
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


def answer_as_provider(
    provider: FakeProvider, nonce: str, *, access_token: str | None
) -> list[httpx.Request]:
    """Answer the token endpoint and ``/me``; return the ``/me`` calls."""
    profile_requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            tokens = {"id_token": id_token(nonce)}
            if access_token is not None:
                tokens["access_token"] = access_token
            return httpx.Response(200, json=tokens)
        if request.url.path.endswith("/me"):
            profile_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "id": "oid-123",
                        "displayName": "Asha Verma",
                        "department": "Finance",
                    },
                },
            )
        return default_reply(request)

    provider.reply = reply
    return profile_requests


async def sign_in_and_return(
    client: AsyncClient,
    setup: dict[str, Any],
    provider: FakeProvider,
    *,
    access_token: str | None,
) -> tuple[dict[str, Any], list[httpx.Request]]:
    started = await start(
        client, setup, flow=FLOW, body={"redirect_uri": REDIRECT_URI}
    )
    assert started.status_code == 201, started.text
    query = query_of(started.json()["next_action"]["url"])
    profile_requests = answer_as_provider(
        provider, query["nonce"], access_token=access_token
    )
    finished = await client.post(
        CALLBACK_URL,
        json={"state": query["state"], "code": "auth-code-1"},
        headers=setup["key"],
    )
    assert finished.status_code == 200, finished.text
    return finished.json(), profile_requests


async def test_steps_call_the_provider_with_the_users_token(
    client: AsyncClient,
    profile: dict[str, Any],
    provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session, profile_requests = await sign_in_and_return(
        client, profile, provider, access_token=USER_ACCESS_TOKEN
    )

    assert session["status"] == "completed"
    assert session["outcome"] == "verified"
    (me,) = profile_requests
    # The user's token replaces the tool's own credential.
    assert me.headers["Authorization"] == f"Bearer {USER_ACCESS_TOKEN}"
    assert API_TOKEN not in str(me.headers)

    result = await client.get(
        session_url(session["id"], "result"), headers=profile["key"]
    )
    assert result.json()["attributes"] == {
        "full_name": "Asha Verma",
        "department": "Finance",
    }
    assert USER_ACCESS_TOKEN not in result.text
    # Nothing kept in the secret store (inputs, result) holds the token.
    async with session_factory() as db:
        stored = (await db.scalars(select(LocalSecret))).all()
    assert stored
    settings = get_settings()
    assert all(
        USER_ACCESS_TOKEN
        not in json.dumps(decrypt_json(secret.ciphertext, settings))
        for secret in stored
    )


async def test_scope_reaches_the_authorize_url(
    client: AsyncClient, profile: dict[str, Any]
) -> None:
    started = await start(
        client, profile, flow=FLOW, body={"redirect_uri": REDIRECT_URI}
    )

    query = query_of(started.json()["next_action"]["url"])
    assert "User.Read" in query["scope"].split()


async def test_missing_access_token_fails_without_calling_the_provider(
    client: AsyncClient, profile: dict[str, Any], provider: FakeProvider
) -> None:
    session, profile_requests = await sign_in_and_return(
        client, profile, provider, access_token=None
    )

    assert session["status"] == "failed"
    assert session["failure_code"] == "provider_error"
    assert profile_requests == []


async def test_flows_without_the_option_keep_the_tools_credential(
    client: AsyncClient, profile: dict[str, Any], provider: FakeProvider
) -> None:
    flow = copy.deepcopy(PROFILE_FLOW)
    flow["authorization"]["steps_as_user"] = False
    saved = await client.put(
        TOOL_URL, json=tool_definition(flow), headers=profile["admin"]
    )
    assert saved.status_code == 200, saved.text

    _, profile_requests = await sign_in_and_return(
        client, profile, provider, access_token=USER_ACCESS_TOKEN
    )

    (me,) = profile_requests
    assert me.headers["Authorization"] == f"Bearer {API_TOKEN}"


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([], "steps_as_user needs at least one step"),
        (
            [{"operation": "fetch_employee", "inputs": ["employee_id"]}],
            "steps_as_user flows may not take inputs",
        ),
    ],
)
async def test_steps_as_user_configuration_is_checked(
    client: AsyncClient,
    setup: dict[str, Any],
    steps: list[dict[str, Any]],
    message: str,
) -> None:
    flow = copy.deepcopy(PROFILE_FLOW)
    flow["steps"] = steps
    flow["outputs"] = {"full_name": "name"}

    response = await client.put(
        TOOL_URL, json=tool_definition(flow), headers=setup["admin"]
    )

    assert response.status_code == 422
    assert message in response.text
