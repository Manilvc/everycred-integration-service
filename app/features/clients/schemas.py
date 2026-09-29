"""Request and response models for clients, API keys, and settings."""

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.models import utc_now
from app.features.clients.models import (
    API_KEY_NAME_MAX_LENGTH,
    CLIENT_CODE_MAX_LENGTH,
    CLIENT_NAME_MAX_LENGTH,
)
from app.features.integration_tools.models import TOOL_CODE_MAX_LENGTH
from app.features.integration_tools.schemas import IntegrationToolSummary
from app.shared.pagination import PaginationParams

# Lower-case words joined by single hyphens or underscores.
CLIENT_CODE_PATTERN = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
# Keeps one client's settings from growing into a document store.
MAX_SETTINGS_BYTES = 16_384


def _strip_required_text(value: str, field_name: str) -> str:
    stripped_value = value.strip()
    if not stripped_value:
        raise ValueError(f"{field_name} must not be blank")
    return stripped_value


class ClientCreate(BaseModel):
    """Details for registering a new client project."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=2,
        max_length=CLIENT_CODE_MAX_LENGTH,
        pattern=CLIENT_CODE_PATTERN,
        examples=["issuer-portal"],
    )
    name: str = Field(min_length=1, max_length=CLIENT_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        """Trim surrounding spaces and reject names that are only spaces."""
        return _strip_required_text(name, "name")


class ClientFilters(PaginationParams):
    """Query parameters for the client listing."""

    include_inactive: bool = False


ClientQuery = Annotated[ClientFilters, Query()]


class ClientResponse(BaseModel):
    """Client project as returned to super admins."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ClientSummary(BaseModel):
    """Identifying fields of a client, as shown to the client itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


class ApiKeyCreate(BaseModel):
    """Options for issuing a new API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=API_KEY_NAME_MAX_LENGTH,
        examples=["production"],
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="Optional expiry, with a time zone. Omit for no expiry.",
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        """Trim surrounding spaces and reject names that are only spaces."""
        return _strip_required_text(name, "name")

    @field_validator("expires_at")
    @classmethod
    def check_expiry_is_in_future(
        cls, expires_at: datetime | None
    ) -> datetime | None:
        """Reject keys that would be expired the moment they are issued."""
        if expires_at is not None and expires_at <= utc_now():
            raise ValueError("expires_at must be in the future")
        return expires_at


class ApiKeyResponse(BaseModel):
    """API key metadata. Never includes the key itself or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned once, when a key is issued."""

    api_key: str = Field(
        description="The full key. It is shown only in this response.",
    )


def _check_settings_size(settings: dict[str, Any]) -> dict[str, Any]:
    encoded_size = len(json.dumps(settings).encode())
    if encoded_size > MAX_SETTINGS_BYTES:
        raise ValueError(
            f"settings must be at most {MAX_SETTINGS_BYTES} bytes as JSON"
        )
    return settings


class IntegrationConfigUpdate(BaseModel):
    """Configuration of one integration type for one client."""

    model_config = ConfigDict(extra="forbid")

    is_enabled: bool = True
    tool_code: str | None = Field(
        default=None,
        max_length=TOOL_CODE_MAX_LENGTH,
        description=(
            "Tool this client uses for the type. Must serve the type and "
            "be active. Omit or send null to clear the choice."
        ),
    )
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret options. Do not put credentials here.",
    )

    @field_validator("settings")
    @classmethod
    def check_settings_size(cls, settings: dict[str, Any]) -> dict[str, Any]:
        """Reject settings larger than ``MAX_SETTINGS_BYTES`` once encoded."""
        return _check_settings_size(settings)


class ClientSettingsUpdate(BaseModel):
    """Settings a client may change for itself.

    Enabling a type and choosing its tool stay with super admins.
    """

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(
        description="Non-secret options. Do not put credentials here.",
    )

    @field_validator("settings")
    @classmethod
    def check_settings_size(cls, settings: dict[str, Any]) -> dict[str, Any]:
        """Reject settings larger than ``MAX_SETTINGS_BYTES`` once encoded."""
        return _check_settings_size(settings)


class IntegrationConfigResponse(BaseModel):
    """A client's configuration for one integration type."""

    integration_type_code: str
    integration_type_name: str
    integration_tool: IntegrationToolSummary | None
    is_enabled: bool
    settings: dict[str, Any]
    updated_at: datetime


class ClientConfigurationResponse(BaseModel):
    """Everything a client needs to know about its own setup."""

    client: ClientSummary
    integrations: list[IntegrationConfigResponse]


CREDENTIAL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_CREDENTIALS = 20
MAX_CREDENTIAL_VALUE_LENGTH = 4096


class ToolCredentialsUpdate(BaseModel):
    """A client's credentials for one tool, replaced as a whole.

    Values go straight to the secret store and are never returned.
    """

    model_config = ConfigDict(extra="forbid")

    credentials: dict[str, str] = Field(
        min_length=1,
        max_length=MAX_CREDENTIALS,
        description=(
            "Names must match the tool's {credentials.<name>} "
            'placeholders, e.g. {"api_token": "..."}.'
        ),
    )

    @field_validator("credentials")
    @classmethod
    def check_credentials(cls, credentials: dict[str, str]) -> dict[str, str]:
        """Validate names and bound value sizes; never echo values."""
        for name, value in credentials.items():
            if not re.fullmatch(CREDENTIAL_NAME_PATTERN, name):
                raise ValueError(
                    f"credential name '{name[:40]}' must be lower_snake_case"
                )
            if not value or len(value) > MAX_CREDENTIAL_VALUE_LENGTH:
                raise ValueError(
                    f"credential '{name}' must be 1 to "
                    f"{MAX_CREDENTIAL_VALUE_LENGTH} characters"
                )
        return credentials


class ToolCredentialsResponse(BaseModel):
    """Which credentials a client has stored for a tool, without values."""

    tool_code: str
    credential_names: list[str]
    created_at: datetime
    updated_at: datetime
