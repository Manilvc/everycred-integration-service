"""Request and response models for the client Integrations screen."""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.connectors.http.config import OPERATION_NAME_PATTERN
from app.features.clients.schemas import (
    CREDENTIAL_NAME_PATTERN,
    MAX_CREDENTIAL_VALUE_LENGTH,
    MAX_CREDENTIALS,
)

MAX_MAPPED_FLOWS = 20
MAX_MAPPINGS_PER_FLOW = 100
_ATTRIBUTE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
# A dot path into the provider's data, or session.<captured value>.
_MAPPING_PATH_PATTERN = r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,15}$"


class CardStatus(StrEnum):
    """What a tool card shows on the Integrations screen.

    ``available``: the tool can be used but nothing is set up.
    ``needs_credentials``: enabled, but required credentials are missing.
    ``not_tested`` / ``configured`` / ``connected`` / ``failed``: the
    last Test connection result (see ``ToolConnectionStatus``).
    ``disabled``: the client switched the tool off.
    ``unavailable``: no connector is installed for the tool yet.
    """

    AVAILABLE = "available"
    NEEDS_CREDENTIALS = "needs_credentials"
    NOT_TESTED = "not_tested"
    CONFIGURED = "configured"
    CONNECTED = "connected"
    FAILED = "failed"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class IntegrationTypeInfo(BaseModel):
    """An integration type heading on the screen."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None


class ToolCard(BaseModel):
    """One tool card inside an integration type group.

    Attributes:
        is_default: This tool is the one the client's users are routed
            through for the type (chosen by a super admin).
    """

    code: str
    name: str
    provider: str | None
    status: CardStatus
    is_enabled: bool
    is_default: bool
    last_tested_at: datetime | None


class IntegrationGroup(BaseModel):
    """Tools available to the client for one integration type."""

    integration_type: IntegrationTypeInfo
    tools: list[ToolCard]


class ClientIntegrationsResponse(BaseModel):
    """Everything the Integrations screen shows, grouped by type."""

    groups: list[IntegrationGroup]


class ToolConnectionDetail(BaseModel):
    """What the tool drawer shows.

    Credential values are never included; only which names are stored
    and which the tool still needs.
    """

    code: str
    name: str
    provider: str | None
    description: str | None
    integration_types: list[IntegrationTypeInfo]
    connector_kind: str | None
    auth_method: str | None = Field(
        description=(
            "oauth2_client_credentials, headers, none, or custom; null "
            "when the tool has no connector"
        ),
    )
    required_credentials: list[str]
    stored_credentials: list[str]
    missing_credentials: list[str]
    can_test: bool
    operations: list[str]
    flows: list[str]
    field_mappings: dict[str, dict[str, str]] = Field(
        description=(
            "Your overrides of flow outputs, as {flow: {attribute: path}}"
        ),
    )
    is_enabled: bool
    status: CardStatus
    last_tested_at: datetime | None
    last_test_message: str | None


class ToolConnectionUpdate(BaseModel):
    """What Save sends from the drawer.

    ``credentials`` are merged into what is stored: send only the ones
    being set or rotated. Values go to the secret store and are never
    returned.
    """

    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    credentials: dict[str, str] | None = Field(
        default=None, max_length=MAX_CREDENTIALS
    )
    remove_credentials: list[str] = Field(
        default_factory=list, max_length=MAX_CREDENTIALS
    )
    field_mappings: dict[str, dict[str, str]] | None = Field(
        default=None,
        max_length=MAX_MAPPED_FLOWS,
        description=(
            "Replaces your output overrides, as {flow: {attribute: path}}. "
            "A path is a dot path into the provider's final response "
            "(e.g. data.full_name) or session.<captured value>. Send {} "
            "to clear them."
        ),
        examples=[{"aadhaar_otp": {"holder_name": "data.full_name"}}],
    )

    @model_validator(mode="after")
    def check_update(self) -> "ToolConnectionUpdate":
        """Require a change, and validate names without echoing values."""
        if (
            self.is_enabled is None
            and not self.credentials
            and not self.remove_credentials
            and self.field_mappings is None
        ):
            raise ValueError("send is_enabled, credentials, or field_mappings")
        for flow, mappings in (self.field_mappings or {}).items():
            if not re.fullmatch(OPERATION_NAME_PATTERN, flow):
                raise ValueError(f"flow name '{flow[:40]}' is not valid")
            if len(mappings) > MAX_MAPPINGS_PER_FLOW:
                raise ValueError(
                    f"at most {MAX_MAPPINGS_PER_FLOW} mappings per flow"
                )
            for attribute, path in mappings.items():
                if not re.fullmatch(_ATTRIBUTE_PATTERN, attribute):
                    raise ValueError(
                        f"attribute '{attribute[:40]}' must be "
                        "lower_snake_case"
                    )
                if not re.fullmatch(_MAPPING_PATH_PATTERN, path):
                    raise ValueError(
                        f"path for '{attribute}' must be a dot path"
                    )
        names = [*(self.credentials or {}), *self.remove_credentials]
        for name in names:
            if not re.fullmatch(CREDENTIAL_NAME_PATTERN, name):
                raise ValueError(
                    f"credential name '{name[:40]}' must be lower_snake_case"
                )
        for name, value in (self.credentials or {}).items():
            if not value or len(value) > MAX_CREDENTIAL_VALUE_LENGTH:
                raise ValueError(
                    f"credential '{name}' must be 1 to "
                    f"{MAX_CREDENTIAL_VALUE_LENGTH} characters"
                )
        overlap = set(self.credentials or {}) & set(self.remove_credentials)
        if overlap:
            raise ValueError(
                "cannot set and remove the same credential: "
                + ", ".join(sorted(overlap))
            )
        return self


class ConnectionTestResponse(BaseModel):
    """Result of Test connection.

    A failed test is a normal result (``success: false``) so the drawer
    can show why; errors are reserved for problems with the request.
    """

    success: bool
    called_provider: bool
    status: CardStatus
    message: str
    tested_at: datetime
