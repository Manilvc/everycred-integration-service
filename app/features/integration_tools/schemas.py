"""Request and response models for integration tools."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.http.config import (
    OPERATION_NAME_PATTERN,
    HttpConnectorConfig,
)
from app.features.integration_tools.models import (
    FIELD_KEY_MAX_LENGTH,
    FIELD_LABEL_MAX_LENGTH,
    PROVIDER_MAX_LENGTH,
    TOOL_NAME_MAX_LENGTH,
    FieldValueType,
)
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.shared.pagination import PaginationParams


class ClientToolFilters(PaginationParams):
    """Query parameters for a client's own tool listing."""

    integration_type: str | None = Field(
        default=None,
        max_length=CODE_MAX_LENGTH,
        description="Only tools serving this integration type code.",
    )


class IntegrationToolFilters(ClientToolFilters):
    """Query parameters for the super admin tool listing."""

    include_inactive: bool = False


MAX_TOOL_FIELDS = 300
# Up to 8 segments of letters, digits, "_" or "-", joined by dots.
FIELD_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,7}$"


class FieldScope(StrEnum):
    """Whether a field applies to every client or to one."""

    GLOBAL = "global"
    CLIENT = "client"


class ToolFieldFilters(PaginationParams):
    """Query parameters for a tool's field keys."""

    flow: str | None = Field(
        default=None,
        pattern=OPERATION_NAME_PATTERN,
        description="Only fields of this flow.",
    )


class ToolFieldInput(BaseModel):
    """One field key a flow's provider response holds.

    Attributes:
        flow: Flow whose response holds the field, e.g. ``aadhaar_otp``.
        key: Dot path in the provider's response data, e.g.
            ``full_name`` or ``address.zip``.
        label: Human-readable name for screens, e.g. "Date of birth".
        value_type: JSON type of the value.
    """

    model_config = ConfigDict(extra="forbid")

    flow: str = Field(pattern=OPERATION_NAME_PATTERN, examples=["aadhaar_otp"])
    key: str = Field(
        max_length=FIELD_KEY_MAX_LENGTH,
        pattern=FIELD_KEY_PATTERN,
        examples=["address.zip"],
    )
    label: str | None = Field(
        default=None, max_length=FIELD_LABEL_MAX_LENGTH, examples=["PIN code"]
    )
    value_type: FieldValueType = FieldValueType.STRING


def _check_unique_fields(fields: list[ToolFieldInput]) -> list[ToolFieldInput]:
    seen: set[tuple[str, str]] = set()
    for field in fields:
        if (field.flow, field.key) in seen:
            raise ValueError(
                f"field '{field.key}' is listed twice for flow '{field.flow}'"
            )
        seen.add((field.flow, field.key))
    return fields


class ToolFieldsUpdate(BaseModel):
    """The complete list of a client's own fields for one tool.

    Replaces the client's earlier list; send ``[]`` to remove them all.
    Global fields are set with the tool definition instead.
    """

    model_config = ConfigDict(extra="forbid")

    fields: list[ToolFieldInput] = Field(max_length=MAX_TOOL_FIELDS)

    @field_validator("fields")
    @classmethod
    def check_unique(
        cls, fields: list[ToolFieldInput]
    ) -> list[ToolFieldInput]:
        """Reject a flow and key listed twice."""
        return _check_unique_fields(fields)


IntegrationToolQuery = Annotated[IntegrationToolFilters, Query()]
ClientToolQuery = Annotated[ClientToolFilters, Query()]
ToolFieldQuery = Annotated[ToolFieldFilters, Query()]


class IntegrationToolFieldResponse(BaseModel):
    """A field key of a tool; only keys are stored, never values.

    Attributes:
        key: Dot path of the field in the provider's response data,
            e.g. ``full_name`` or ``address.zip``.
        scope: ``global`` for every client, ``client`` for one client.
    """

    flow: str
    key: str
    label: str | None
    value_type: FieldValueType
    scope: FieldScope
    created_at: datetime
    updated_at: datetime


class IntegrationTypeSummary(BaseModel):
    """An integration type a tool serves."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class ConnectorParameterResponse(BaseModel):
    """One argument the tool's connector accepts."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    kind: str = Field(
        description=(
            "positional_or_keyword, positional_only, keyword_only, "
            "var_positional, or var_keyword"
        ),
    )
    required: bool
    annotation: str | None


class OperationDescriptionResponse(BaseModel):
    """An operation the tool offers and the inputs it reads."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str | None
    required_kwargs: list[str]
    required_credentials: list[str]


class FlowDescriptionResponse(BaseModel):
    """A verification or gather flow the tool offers.

    Attributes:
        inputs: Everything the holder may be asked for, in order. Send
            what you already have when starting a session; the rest is
            requested through ``awaiting_inputs``.
        outputs: Attribute names a completed session returns.
    """

    name: str
    purpose: str
    description: str | None
    inputs: list[str]
    outputs: list[str]


class ConnectorInfo(BaseModel):
    """Whether the tool can be connected yet, and how to call it.

    Attributes:
        is_available: A connector exists for the tool.
        kind: ``code`` for a Python connector, ``http`` for one driven
            by the tool's configuration.
        parameters: Arguments of a Python connector's ``connect``.
        operations: Operations a client can run for its users.
        flows: Verification and gather flows, run as sessions.
    """

    is_available: bool
    kind: str | None = None
    parameters: list[ConnectorParameterResponse] = Field(default_factory=list)
    operations: list[OperationDescriptionResponse] = Field(
        default_factory=list
    )
    flows: list[FlowDescriptionResponse] = Field(default_factory=list)


class IntegrationToolSummary(BaseModel):
    """Identifying fields of a tool, used inside other responses."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    provider: str | None


class IntegrationToolResponse(BaseModel):
    """Integration tool as returned by the listing endpoints."""

    id: uuid.UUID
    code: str
    name: str
    provider: str | None
    description: str | None
    is_active: bool
    display_order: int
    integration_types: list[IntegrationTypeSummary]
    connector: ConnectorInfo
    created_at: datetime
    updated_at: datetime


class IntegrationToolUpsert(BaseModel):
    """Definition of a tool, created or replaced as a whole."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=TOOL_NAME_MAX_LENGTH)
    provider: str | None = Field(default=None, max_length=PROVIDER_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    display_order: int = Field(default=0, ge=0, le=100_000)
    integration_types: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Codes of the integration types this tool serves.",
    )
    connector_config: HttpConnectorConfig | None = Field(
        default=None,
        description=(
            "Configuration for the generic HTTP connector. Leave empty "
            "for tools served by a Python connector. Must not contain "
            "secrets; use {credentials.<name>} placeholders instead."
        ),
    )
    fields: list[ToolFieldInput] | None = Field(
        default=None,
        max_length=MAX_TOOL_FIELDS,
        description=(
            "Global field keys the provider returns, for every client. "
            "Replaces the tool's earlier global list; leave out to keep "
            "it unchanged, send [] to remove it."
        ),
    )

    @field_validator("fields")
    @classmethod
    def check_unique_fields(
        cls, fields: list[ToolFieldInput] | None
    ) -> list[ToolFieldInput] | None:
        """Reject a flow and key listed twice."""
        return None if fields is None else _check_unique_fields(fields)

    @field_validator("integration_types")
    @classmethod
    def deduplicate_types(cls, codes: list[str]) -> list[str]:
        """Keep the first occurrence of each code, in order."""
        return list(dict.fromkeys(codes))
