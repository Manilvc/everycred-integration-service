"""Choose the connector that serves an integration tool.

A tool is served by, in order of preference:

1. a Python connector registered under the tool's code, for providers
   that need custom logic; or
2. the generic :class:`~app.connectors.http.connector.HttpConnector`,
   when the tool's ``connector_config`` has ``"type": "http"``.

Tools with neither cannot be connected yet.
"""

from enum import StrEnum
from typing import Any

from app.connectors.base import IntegrationConnector
from app.connectors.http.connector import HttpConnector
from app.connectors.registry import ConnectorRegistry


class ConnectorKind(StrEnum):
    """How a tool's connector is implemented."""

    CODE = "code"
    HTTP = "http"


def resolve_connector(
    tool_code: str,
    tool_config: dict[str, Any] | None,
    registry: ConnectorRegistry,
) -> type[IntegrationConnector] | None:
    """Return the connector class for a tool, or None if it has none."""
    registered = registry.get(tool_code)
    if registered is not None:
        return registered
    if tool_config and tool_config.get("type") == ConnectorKind.HTTP:
        return HttpConnector
    return None


def connector_kind(
    connector_class: type[IntegrationConnector],
) -> ConnectorKind:
    """Say whether a connector is Python code or configuration."""
    if connector_class is HttpConnector:
        return ConnectorKind.HTTP
    return ConnectorKind.CODE
