import pytest

from app.connectors.base import (
    ConnectionOutcome,
    IntegrationConnector,
    check_arguments_fit,
)
from app.connectors.registry import ConnectorRegistry


class TokenConnector(IntegrationConnector):
    async def connect(
        self, api_token: str, *, region: str = "in"
    ) -> ConnectionOutcome:
        return ConnectionOutcome()


class OtherConnector(IntegrationConnector):
    async def connect(self) -> ConnectionOutcome:
        return ConnectionOutcome()


def test_registry_returns_registered_connector() -> None:
    registry = ConnectorRegistry()
    registry.register("confirm", TokenConnector)

    assert registry.get("confirm") is TokenConnector
    assert registry.get("gather") is None
    assert registry.registered_codes() == ["confirm"]


def test_registering_a_second_connector_for_a_code_fails() -> None:
    registry = ConnectorRegistry()
    registry.register("confirm", TokenConnector)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("confirm", OtherConnector)


def test_re_registering_the_same_connector_is_allowed() -> None:
    registry = ConnectorRegistry()
    registry.register("confirm", TokenConnector)
    registry.register("confirm", TokenConnector)

    assert registry.get("confirm") is TokenConnector


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        (["token"], {}),
        (["token"], {"region": "eu"}),
        ([], {"api_token": "token"}),
    ],
)
def test_matching_arguments_fit(args: list, kwargs: dict) -> None:
    assert check_arguments_fit(TokenConnector, args, kwargs) is None


@pytest.mark.parametrize(
    ("args", "kwargs", "reason"),
    [
        ([], {}, "missing"),
        (["token", "extra"], {}, "too many"),
        (["token"], {"unknown": 1}, "unexpected"),
        (["token"], {"api_token": "again"}, "multiple values"),
        (["token", "eu"], {}, "too many"),
    ],
)
def test_mismatched_arguments_are_explained(
    args: list, kwargs: dict, reason: str
) -> None:
    mismatch = check_arguments_fit(TokenConnector, args, kwargs)

    assert mismatch is not None
    assert reason in mismatch
