"""Request and response models for integration types."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from app.features.integration_types.models import IntegrationDirection
from app.shared.pagination import PaginationParams


class DirectionFilter(StrEnum):
    """Which integration types a listing returns, by direction."""

    ALL = "all"
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class IntegrationTypeFilters(PaginationParams):
    """Query parameters accepted by the integration type listing."""

    include_inactive: bool = False
    direction: DirectionFilter = Field(
        default=DirectionFilter.ALL,
        description="`inbound`, `outbound`, or `all` (the default).",
    )


class ClientIntegrationTypeFilters(PaginationParams):
    """Query parameters for a client's integration type listing."""

    direction: DirectionFilter = Field(
        default=DirectionFilter.ALL,
        description="`inbound`, `outbound`, or `all` (the default).",
    )


IntegrationTypeQuery = Annotated[IntegrationTypeFilters, Query()]
ClientIntegrationTypeQuery = Annotated[ClientIntegrationTypeFilters, Query()]


class IntegrationTypeResponse(BaseModel):
    """Integration type as returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    display_order: int
    direction: IntegrationDirection
    created_at: datetime
    updated_at: datetime


class ClientIntegrationTypeResponse(IntegrationTypeResponse):
    """An integration type as a client sees it.

    Attributes:
        is_enabled: A super admin has enabled the type for this client.
    """

    is_enabled: bool
