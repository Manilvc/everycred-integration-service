"""Request and response models for integration tools."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.http.config import HttpConnectorConfig
from app.features.integration_tools.models import (
    PROVIDER_MAX_LENGTH,
    TOOL_NAME_MAX_LENGTH,
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


IntegrationToolQuery = Annotated[IntegrationToolFilters, Query()]
ClientToolQuery = Annotated[ClientToolFilters, Query()]


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


class ConnectorInfo(BaseModel):
    """Whether the tool can be connected yet, and how to call it.

    Attributes:
        is_available: A connector exists for the tool.
        kind: ``code`` for a Python connector, ``http`` for one driven
            by the tool's configuration.
        parameters: Arguments of a Python connector's ``connect``.
        operations: Operations a client can run for its users.
    """

    is_available: bool
    kind: str | None = None
    parameters: list[ConnectorParameterResponse] = Field(default_factory=list)
    operations: list[OperationDescriptionResponse] = Field(
        default_factory=list
    )


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

    @field_validator("integration_types")
    @classmethod
    def deduplicate_types(cls, codes: list[str]) -> list[str]:
        """Keep the first occurrence of each code, in order."""
        return list(dict.fromkeys(codes))
