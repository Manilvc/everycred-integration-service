import base64
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from app.connectors.base import ConnectorContext, ConnectorError
from app.connectors.http import oauth
from app.connectors.http.config import HttpConnectorConfig
from app.connectors.http.connector import HttpConnector

TOKEN_URL = "https://login.example.com/{credentials.tenant_id}/oauth2/token"
CREDENTIALS = {
    "tenant_id": "tenant-1",
    "client_id": "app-123",
    "client_secret": "s3cret-value",
}


def oauth_config(**auth_changes: object) -> dict:
    return {
        "type": "http",
        "environments": {"prod": "https://graph.example.com/v1"},
        "default_environment": "prod",
        "auth": {
            "type": "oauth2_client_credentials",
            "token_url": TOKEN_URL,
            "scope": "https://graph.example.com/.default",
            **auth_changes,
        },
        "operations": {"me": {"method": "GET", "path": "/me"}},
    }


class FakeProvider:
    def __init__(self) -> None:
        self.token_requests: list[httpx.Request] = []
        self.api_requests: list[httpx.Request] = []
        self.token_status = 200
        self.reject_next_api_call = False
        self.issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.example.com":
            self.token_requests.append(request)
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status,
                    json={"error": "invalid_client", "secret": "leak?"},
                )
            self.issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{self.issued}",
                    "expires_in": 3600,
                },
            )
        self.api_requests.append(request)
        if self.reject_next_api_call:
            self.reject_next_api_call = False
            return httpx.Response(401, json={"success": False})
        return httpx.Response(200, json={"success": True, "data": {"id": 1}})


@pytest.fixture(autouse=True)
def fresh_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth, "token_cache", oauth.TokenCache())


def make_connector(
    provider: FakeProvider,
    config: dict | None = None,
    credentials: dict | None = None,
) -> HttpConnector:
    return HttpConnector(
        ConnectorContext(
            client_id=uuid.UUID(int=1),
            user_uuid=None,
            integration_type_code="confirm",
            tool_code="entra",
            client_settings={},
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(provider)
            ),
            credentials=CREDENTIALS if credentials is None else credentials,
            tool_config=config or oauth_config(),
        )
    )


async def test_token_is_requested_once_and_sent_as_bearer() -> None:
    provider = FakeProvider()
    connector = make_connector(provider)

    await connector.run_operation("me")
    await connector.run_operation("me")

    assert len(provider.token_requests) == 1
    token_request = provider.token_requests[0]
    assert token_request.url.path == "/tenant-1/oauth2/token"
    assert parse_qs(token_request.content.decode()) == {
        "grant_type": ["client_credentials"],
        "scope": ["https://graph.example.com/.default"],
        "client_id": ["app-123"],
        "client_secret": ["s3cret-value"],
    }
    assert all(
        request.headers["Authorization"] == "Bearer token-1"
        for request in provider.api_requests
    )


async def test_basic_client_auth_keeps_secret_out_of_body() -> None:
    provider = FakeProvider()
    connector = make_connector(provider, oauth_config(client_auth="basic"))

    await connector.run_operation("me")

    token_request = provider.token_requests[0]
    expected = base64.b64encode(b"app-123:s3cret-value").decode()
    assert token_request.headers["Authorization"] == f"Basic {expected}"
    assert "client_secret" not in token_request.content.decode()


async def test_401_with_cached_token_retries_with_a_new_one() -> None:
    provider = FakeProvider()
    connector = make_connector(provider)
    await connector.run_operation("me")
    provider.reject_next_api_call = True

    outcome = await connector.run_operation("me")

    assert outcome.success is True
    assert provider.api_requests[-1].headers["Authorization"] == (
        "Bearer token-2"
    )


async def test_rotated_secret_gets_a_new_token() -> None:
    provider = FakeProvider()
    await make_connector(provider).run_operation("me")

    rotated = {**CREDENTIALS, "client_secret": "new-secret"}
    await make_connector(provider, credentials=rotated).run_operation("me")

    assert len(provider.token_requests) == 2


async def test_rejected_credentials_do_not_leak_response() -> None:
    provider = FakeProvider()
    provider.token_status = 401
    connector = make_connector(provider)

    with pytest.raises(ConnectorError) as error:
        await connector.run_operation("me")

    assert "rejected the client credentials" in str(error.value)
    assert "leak" not in str(error.value)
    assert provider.api_requests == []


async def test_connection_test_always_fetches_a_fresh_token() -> None:
    provider = FakeProvider()
    connector = make_connector(provider)
    await connector.run_operation("me")

    outcome = await connector.test_connection()

    assert outcome.called_provider is True
    assert len(provider.token_requests) == 2


async def test_connection_test_without_auth_only_checks_credentials() -> None:
    provider = FakeProvider()
    config = {
        "type": "http",
        "environments": {"prod": "https://api.example.com"},
        "default_environment": "prod",
        "headers": {"X-Api-Key": "{credentials.api_key}"},
    }
    connector = make_connector(provider, config, {"api_key": "k"})

    outcome = await connector.test_connection()

    assert outcome.called_provider is False
    assert provider.token_requests == provider.api_requests == []


def test_requirements_describe_oauth() -> None:
    requirements = HttpConnector.describe_requirements(oauth_config())

    assert requirements.auth_method == "oauth2_client_credentials"
    assert requirements.required_credentials == [
        "client_id",
        "client_secret",
        "tenant_id",
    ]
    assert requirements.can_test is True


@pytest.mark.parametrize(
    ("auth_changes", "message"),
    [
        ({"token_url": "https://{credentials.host}/token"}, "fixed host"),
        ({"token_url": "http://login.example.com/token"}, "must use https"),
        ({"client_secret": "hard-coded"}, "client_secret must be exactly"),
        ({"client_id": "literal-id"}, "client_id must come from"),
        ({"token_url": "https://login.example.com/{kwargs.x}"}, "token_url"),
    ],
)
def test_invalid_oauth_configs_are_rejected(
    auth_changes: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        HttpConnectorConfig.model_validate(oauth_config(**auth_changes))


def test_auth_and_authorization_header_conflict() -> None:
    config = oauth_config()
    config["headers"] = {"Authorization": "Bearer {credentials.token}"}

    with pytest.raises(ValidationError, match="remove it from headers"):
        HttpConnectorConfig.model_validate(config)


def test_test_operation_may_not_use_user_inputs() -> None:
    config = oauth_config()
    config["operations"]["lookup"] = {"path": "/users/{kwargs.id}"}
    config["test_operation"] = "lookup"

    with pytest.raises(ValidationError, match="runs without a user"):
        HttpConnectorConfig.model_validate(config)
