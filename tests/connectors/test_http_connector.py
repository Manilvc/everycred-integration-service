import json
import uuid
from collections.abc import Callable

import httpx
import pytest

from app.connectors.base import (
    ConnectorContext,
    ConnectorError,
    InvalidInputError,
    MissingParametersError,
    UnknownOperationError,
)
from app.connectors.http.connector import HttpConnector
from tests.connectors.test_http_config import VALID_CONFIG

CLIENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
USER_UUID = uuid.UUID("00000000-0000-4000-8000-000000000002")
CREDENTIALS = {"api_token": "tok-secret", "customer_id": "cust-9"}


def make_connector(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: dict | None = None,
    credentials: dict | None = None,
    captured: list[httpx.Request] | None = None,
) -> HttpConnector:
    def recording_handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(recording_handler)
    )
    return HttpConnector(
        ConnectorContext(
            client_id=CLIENT_ID,
            user_uuid=USER_UUID,
            integration_type_code="confirm",
            tool_code="acme",
            client_settings=settings or {},
            http_client=http_client,
            credentials=CREDENTIALS if credentials is None else credentials,
            tool_config=VALID_CONFIG,
        )
    )


def json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_request_is_built_from_configuration() -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True, "data": {"ok": 1}}),
        captured=captured,
    )

    await connector.run_operation("verify_document", id_number="ABCDE1234F")

    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://sandbox.example.com/api/v1/documents/verify"
    )
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert request.headers["X-Customer-Id"] == "cust-9"
    assert json.loads(request.content) == {"id_number": "ABCDE1234F"}


async def test_client_setting_selects_environment() -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True, "data": {}}),
        settings={"environment": "production"},
        captured=captured,
    )

    await connector.run_operation("verify_document", id_number="X")

    assert captured[0].url.host == "api.example.com"


async def test_unknown_environment_setting_is_refused() -> None:
    connector = make_connector(
        lambda _: json_response({}),
        settings={"environment": "https://attacker.example.com"},
    )

    with pytest.raises(ConnectorError, match="not offered"):
        await connector.run_operation("verify_document", id_number="X")


async def test_path_inputs_are_url_encoded() -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True, "data": {}}),
        settings={"region": "in"},
        captured=captured,
    )

    await connector.run_operation("fetch_record", record_id="../../admin?x=1")

    request = captured[0]
    assert request.method == "GET"
    assert request.url.raw_path.startswith(
        b"/api/v1/records/..%2F..%2Fadmin%3Fx%3D1"
    )
    assert request.url.params["region"] == "in"
    assert request.content == b""


async def test_response_is_normalised() -> None:
    connector = make_connector(
        lambda _: json_response(
            {"success": True, "data": {"name": "A"}, "message": "ok"}
        )
    )

    outcome = await connector.run_operation("verify_document", id_number="X")

    assert outcome.success is True
    assert outcome.status_code == 200
    assert outcome.data == {"name": "A"}
    assert outcome.message == "ok"


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({"success": False, "data": None, "message": "Invalid"}, 200),
        ({"success": True, "data": {}, "message": "Bad request"}, 422),
    ],
)
async def test_provider_rejection_is_an_outcome_not_an_error(
    payload: dict, status_code: int
) -> None:
    connector = make_connector(lambda _: json_response(payload, status_code))

    outcome = await connector.run_operation("verify_document", id_number="X")

    assert outcome.success is False
    assert outcome.status_code == status_code


async def test_non_json_response_raises() -> None:
    connector = make_connector(
        lambda _: httpx.Response(502, text="<html>Bad gateway</html>")
    )

    with pytest.raises(ConnectorError, match="non-JSON"):
        await connector.run_operation("verify_document", id_number="X")


async def test_transport_failure_raises_without_details() -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.1")

    connector = make_connector(fail)

    with pytest.raises(ConnectorError) as error:
        await connector.run_operation("verify_document", id_number="X")

    assert "10.0.0.1" not in str(error.value)


async def test_timeout_raises_connector_error() -> None:
    def slow(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    connector = make_connector(slow)

    with pytest.raises(ConnectorError, match="in time"):
        await connector.run_operation("verify_document", id_number="X")


async def test_missing_inputs_and_credentials_are_reported() -> None:
    connector = make_connector(lambda _: json_response({}), credentials={})

    with pytest.raises(MissingParametersError) as error:
        await connector.run_operation("verify_document")

    assert error.value.missing == [
        "credentials.api_token",
        "credentials.customer_id",
        "kwargs.id_number",
    ]


async def test_unknown_operation_raises() -> None:
    connector = make_connector(lambda _: json_response({}))

    with pytest.raises(UnknownOperationError):
        await connector.run_operation("delete_everything")


async def test_connect_runs_connect_operation() -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True, "data": {}}),
        captured=captured,
    )

    outcome = await connector.connect(id_number="X")

    assert outcome.details == {"environment": "sandbox"}
    assert captured[0].url.path.endswith("/documents/verify")


async def test_connect_fails_when_provider_rejects() -> None:
    connector = make_connector(
        lambda _: json_response({"success": False, "message": "Bad token"})
    )

    with pytest.raises(ConnectorError, match="Bad token"):
        await connector.connect(id_number="X")


def test_describe_operations_lists_inputs() -> None:
    operations = {
        operation.name: operation
        for operation in HttpConnector.describe_operations(VALID_CONFIG)
    }

    assert operations["verify_document"].required_kwargs == ["id_number"]
    assert operations["verify_document"].required_credentials == [
        "api_token",
        "customer_id",
    ]
    assert operations["verify_document"].description == (
        "Check a document number"
    )


@pytest.mark.parametrize("record_id", ["..", ".", "", " .. "])
async def test_dot_segments_in_path_inputs_are_rejected(
    record_id: str,
) -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True}),
        settings={"region": "in"},
        captured=captured,
    )

    with pytest.raises(InvalidInputError) as error:
        await connector.run_operation("fetch_record", record_id=record_id)

    assert error.value.names == ["kwargs.record_id"]
    assert captured == []


@pytest.mark.parametrize("record_id", ["%2E%2E", "..%2F..", "a/../../b"])
async def test_encoded_traversal_stays_inside_the_path(record_id: str) -> None:
    captured: list[httpx.Request] = []
    connector = make_connector(
        lambda _: json_response({"success": True}),
        settings={"region": "in"},
        captured=captured,
    )

    await connector.run_operation("fetch_record", record_id=record_id)

    assert captured[0].url.path.startswith("/api/v1/records/")
    assert captured[0].url.host == "sandbox.example.com"
