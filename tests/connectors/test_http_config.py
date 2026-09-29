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
