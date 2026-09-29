import copy

import pytest
from pydantic import ValidationError

from app.connectors.http.config import HttpConnectorConfig

VALID_CONFIG = {
    "type": "http",
    "environments": {
        "sandbox": "https://sandbox.example.com/api/v1/",
        "production": "https://api.example.com/api/v1",
    },
    "default_environment": "sandbox",
    "headers": {
        "Authorization": "Bearer {credentials.api_token}",
        "X-Customer-Id": "{credentials.customer_id}",
    },
    "operations": {
        "verify_document": {
            "method": "POST",
            "path": "/documents/verify",
            "description": "Check a document number",
            "body": {"id_number": "{kwargs.id_number}"},
        },
        "fetch_record": {
            "method": "GET",
            "path": "/records/{kwargs.record_id}",
            "query": {"region": "{settings.region}"},
        },
    },
    "connect_operation": "verify_document",
}


def with_changes(**changes: object) -> dict:
    config = copy.deepcopy(VALID_CONFIG)
    config.update(changes)
    return config


def test_valid_config_is_accepted_and_urls_normalised() -> None:
    config = HttpConnectorConfig.model_validate(VALID_CONFIG)

    assert config.environments["sandbox"] == (
        "https://sandbox.example.com/api/v1"
    )
    assert config.required_inputs("verify_document") == {
        "kwargs.id_number",
        "credentials.api_token",
        "credentials.customer_id",
    }


def test_http_is_allowed_only_for_local_servers() -> None:
    local = with_changes(
        environments={"local": "http://127.0.0.1:9000"},
        default_environment="local",
    )

    assert HttpConnectorConfig.model_validate(local)
    with pytest.raises(ValidationError, match="must use https"):
        HttpConnectorConfig.model_validate(
            with_changes(
                environments={"prod": "http://api.example.com"},
                default_environment="prod",
            )
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"default_environment": "staging"}, "default_environment"),
        ({"connect_operation": "missing"}, "connect_operation"),
        (
            {"headers": {"Authorization": "Bearer hard-coded-token"}},
            "must take its value from a",
        ),
        (
            {"headers": {"X-Api-Key": "{settings.key}"}},
            "must take its value from a",
        ),
        (
            {"headers": {"X-Trace": "{kwargs.trace}"}},
            "not user inputs",
        ),
        (
            {"environments": {"sandbox": "https://x.example.com?a=1"}},
            "must be a plain",
        ),
    ],
)
def test_invalid_configs_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HttpConnectorConfig.model_validate(with_changes(**changes))


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ({"path": "https://evil.example.com/x"}, "relative"),
        ({"path": "//evil.example.com/x"}, "relative"),
        ({"path": "no-leading-slash"}, "relative"),
        ({"path": "/x/{credentials.api_token}"}, "may not use credentials"),
        ({"path": "/x", "body": {"a": "{env.HOME}"}}, "unknown placeholder"),
        ({"path": "/x", "timeout": 5}, "Extra inputs"),
    ],
)
def test_invalid_operations_are_rejected(
    operation: dict, message: str
) -> None:
    config = with_changes(operations={"op": operation}, connect_operation=None)

    with pytest.raises(ValidationError, match=message):
        HttpConnectorConfig.model_validate(config)


def test_operation_names_must_be_snake_case() -> None:
    config = with_changes(
        operations={"Verify-Doc": {"path": "/x"}}, connect_operation=None
    )

    with pytest.raises(ValidationError, match="lower_snake_case"):
        HttpConnectorConfig.model_validate(config)


VALID_FLOW = {
    "purpose": "verification",
    "steps": [{"operation": "verify_document", "inputs": ["id_number"]}],
    "outputs": {"status": "status"},
    "verified_when": [{"path": "status", "equals": "valid"}],
}


def test_valid_flow_is_accepted() -> None:
    config = HttpConnectorConfig.model_validate(
        with_changes(flows={"document_check": VALID_FLOW})
    )

    assert config.flows["document_check"].all_inputs() == ["id_number"]


@pytest.mark.parametrize(
    ("flow", "message"),
    [
        (
            {**VALID_FLOW, "steps": [{"operation": "missing"}]},
            "unknown operation",
        ),
        (
            {**VALID_FLOW, "steps": [{"operation": "verify_document"}]},
            "no step asks for",
        ),
        (
            {**VALID_FLOW, "outputs": {"ref": "session.never_captured"}},
            "never captured",
        ),
        (
            {"purpose": "gather", "steps": VALID_FLOW["steps"]},
            "must declare outputs",
        ),
        (
            {**VALID_FLOW, "purpose": "gather"},
            "verified_when applies",
        ),
    ],
)
def test_invalid_flows_are_rejected(flow: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HttpConnectorConfig.model_validate(
            with_changes(flows={"document_check": flow})
        )


def test_flow_step_may_read_values_captured_earlier() -> None:
    config = with_changes(
        operations={
            **VALID_CONFIG["operations"],
            "confirm": {
                "path": "/confirm",
                "body": {"ref": "{session.ref}", "otp": "{kwargs.otp}"},
            },
        },
        flows={
            "two_step": {
                "purpose": "verification",
                "steps": [
                    {
                        "operation": "verify_document",
                        "inputs": ["id_number"],
                        "capture": {"ref": "reference"},
                    },
                    {"operation": "confirm", "inputs": ["otp"]},
                ],
            }
        },
    )

    assert HttpConnectorConfig.model_validate(config).flows["two_step"]


def test_session_values_are_not_allowed_in_headers() -> None:
    with pytest.raises(ValidationError, match="not user inputs"):
        HttpConnectorConfig.model_validate(
            with_changes(headers={"X-Ref": "{session.ref}"})
        )


def test_flow_inputs_may_not_use_reserved_names() -> None:
    flow = {
        **VALID_FLOW,
        "steps": [{"operation": "verify_document", "inputs": ["operation"]}],
    }

    with pytest.raises(ValidationError, match="reserved"):
        HttpConnectorConfig.model_validate(
            with_changes(flows={"document_check": flow})
        )
