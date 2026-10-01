"""Connector for the Holder Wallet App, EveryCRED's own wallet.

Holders declare their details in the wallet, so there is no external
provider to connect to. The tool exists so the Declare group of the
Integrations screen lists the wallet like any other system; it is
seeded by migration and marked built in.
"""

from typing import Any

from app.connectors.base import (
    ConnectionOutcome,
    ConnectionRequirements,
    IntegrationConnector,
)
from app.connectors.registry import register_connector

HOLDER_WALLET_APP_CODE = "holder-wallet-app"


@register_connector(HOLDER_WALLET_APP_CODE)
class HolderWalletAppConnector(IntegrationConnector):
    """Built-in connector: always available, nothing to configure."""

    is_built_in = True

    async def connect(self) -> ConnectionOutcome:
        """Accept every user; the wallet needs no per-user setup."""
        return ConnectionOutcome()

    @classmethod
    def describe_requirements(
        cls, tool_config: dict[str, Any] | None
    ) -> ConnectionRequirements:
        """Report that no credentials are needed."""
        return ConnectionRequirements(
            auth_method="none", required_credentials=[], can_test=False
        )
