"""Request and response models for the client Integrations screen."""

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.clients.schemas import (
    CREDENTIAL_NAME_PATTERN,
    MAX_CREDENTIAL_VALUE_LENGTH,
    MAX_CREDENTIALS,
)

LISTING_MESSAGE = "Integrations retrieved successfully."
USER_LISTING_MESSAGE = "User integrations retrieved successfully."


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


class UserIntegrationStatus(StrEnum):
    """Where one user stands with one integration.

    ``not_connected``: the user has no connection for the type yet.
    ``pending``: parameters saved, not connected since.
    ``connected`` / ``failed``: result of the last connection attempt.
    ``unavailable``: the user cannot connect, because the client has
    switched the tool off, has not stored its credentials, or no
    connector is installed.
    """

    NOT_CONNECTED = "not_connected"
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class IntegrationTypeInfo(BaseModel):
    """An integration type heading on the screen."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None


class IntegrationSystem(BaseModel):
    """One tool ("system") inside an integration type group.

    Field names follow the EveryCRED Integrations screen, so its
    frontend can render this response as it renders its own.

    Attributes:
        id: The tool's id.
        code: The tool's code, used by the drawer, Save, and Test
            connection endpoints.
        source_role_id: Id of the integration type (group) it is listed
            under.
        status_note: Short subtitle under the name; the tool's
            description.
        is_connected: The last Test connection succeeded, or the tool is
            built in.
        is_active: The tool is switched on for the client.
        is_default: Built in to EveryCRED, or the tool the client's
            users are routed through for this type.
        last_tested_at: When Test connection last ran, if ever.
    """

    id: uuid.UUID
    code: str
    source_role_id: uuid.UUID
    name: str
    status_note: str | None
    is_connected: bool
    is_active: bool
    is_default: bool
    status: CardStatus
    last_tested_at: datetime | None


class IntegrationGroup(BaseModel):
    """One integration type and the client's systems for it.

    Attributes:
        id: The integration type's id.
        key: The integration type's code, e.g. ``confirm``.
        label: ``"<NAME> - <description>"``, e.g.
            ``"CONFIRM - Identity & verification"``.
    """

    id: uuid.UUID
    key: str
    label: str
    description: str | None
    systems: list[IntegrationSystem]


class UserIntegrationSystem(BaseModel):
    """A tool one user can connect through, with the user's status.

    Same fields as :class:`IntegrationSystem` where they mean the same
    thing, so the EveryCRED frontend can reuse its components.

    Attributes:
        is_connected: The user's last connection attempt succeeded.
        is_active: The user can use the tool: the client has it set up
            and switched on.
        is_default: Always true here; each type lists the tool its users
            are routed through, plus built-in tools.
        status: The user's status with this integration.
        tool_status: The tool's status for the client, as on the
            client's Integrations screen.
        connection_id: The user's connection, if one exists.
        last_error: Safe reason for the last failed attempt.
    """

    id: uuid.UUID
    code: str
    source_role_id: uuid.UUID
    name: str
    status_note: str | None
    is_connected: bool
    is_active: bool
    is_default: bool
    status: UserIntegrationStatus
    tool_status: CardStatus
    connection_id: uuid.UUID | None
    last_attempt_at: datetime | None
    last_connected_at: datetime | None
    last_error: str | None


class UserIntegrationGroup(BaseModel):
    """One integration type and the tools a user can connect through."""

    id: uuid.UUID
    key: str
    label: str
    description: str | None
    systems: list[UserIntegrationSystem]


class UserIntegrationsListing(BaseModel):
    """Every active integration type for one of the client's users."""

    user_uuid: uuid.UUID
    groups: list[UserIntegrationGroup]


class UserIntegrationsListingResponse(BaseModel):
    """A user's integrations, wrapped like EveryCRED's own responses."""

    status: Literal["success"] = "success"
    data: UserIntegrationsListing
    message: str = USER_LISTING_MESSAGE


class IntegrationsListing(BaseModel):
    """Every active integration type, in display order."""

    groups: list[IntegrationGroup]


class IntegrationsListingResponse(BaseModel):
    """The Integrations screen, wrapped like EveryCRED's own responses."""

    status: Literal["success"] = "success"
    data: IntegrationsListing
    message: str = LISTING_MESSAGE


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

    @model_validator(mode="after")
    def check_update(self) -> "ToolConnectionUpdate":
        """Require a change, and validate names without echoing values."""
        if (
            self.is_enabled is None
            and not self.credentials
            and not self.remove_credentials
        ):
            raise ValueError("send is_enabled, credentials, or both")
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
