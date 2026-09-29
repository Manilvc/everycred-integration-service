"""FastAPI dependency that provides the connector registry."""

from typing import Annotated

from fastapi import Depends

import app.connectors  # noqa: F401 - imports connector modules so they register
from app.connectors.registry import ConnectorRegistry, default_registry


def get_connector_registry() -> ConnectorRegistry:
    """Return the registry of connectors; tests override this."""
    return default_registry


ConnectorRegistryDep = Annotated[
    ConnectorRegistry, Depends(get_connector_registry)
]
