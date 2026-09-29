"""Lookup from integration tool code to connector class.

Integration tools are data, but connectors are code. The registry is
the bridge: a connector class registers itself under a tool code, and
the connection service asks the registry which class handles the tool
a client has chosen.

Register a connector with the decorator::

    @register_connector("acme-kyc")
    class AcmeKycConnector(IntegrationConnector):
        async def connect(self, api_token: str, *, region: str = "in"):
            ...

and import its module in :mod:`app.connectors` so the decorator runs.
"""

from app.connectors.base import IntegrationConnector


class ConnectorRegistry:
    """Maps integration tool codes to connector classes."""

    def __init__(self) -> None:
        self._connectors: dict[str, type[IntegrationConnector]] = {}

    def register(
        self, tool_code: str, connector: type[IntegrationConnector]
    ) -> None:
        """Make ``connector`` handle the tool ``tool_code``.

        Raises:
            ValueError: Another connector already handles the code.
                Silently replacing it would route users to the wrong
                tool.
        """
        existing = self._connectors.get(tool_code)
        if existing is not None and existing is not connector:
            raise ValueError(
                f"{existing.__name__} is already registered for '{tool_code}'"
            )
        self._connectors[tool_code] = connector

    def get(self, tool_code: str) -> type[IntegrationConnector] | None:
        """Return the connector for a tool, or None if none is registered."""
        return self._connectors.get(tool_code)

    def registered_codes(self) -> list[str]:
        """Return every code that has a connector, sorted."""
        return sorted(self._connectors)


default_registry = ConnectorRegistry()


def register_connector(tool_code: str):
    """Class decorator that registers a connector in the default registry.

    ``tool_code`` must match an ``integration_tools.code``.
    """

    def decorator(
        connector: type[IntegrationConnector],
    ) -> type[IntegrationConnector]:
        default_registry.register(tool_code, connector)
        return connector

    return decorator
