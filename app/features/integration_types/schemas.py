"""Request and response models for integration types."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict

from app.shared.pagination import PaginationParams


class IntegrationTypeFilters(PaginationParams):
    """Query parameters accepted by the integration type listing."""

    include_inactive: bool = False


IntegrationTypeQuery = Annotated[IntegrationTypeFilters, Query()]


class IntegrationTypeResponse(BaseModel):
    """Integration type as returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: datetime
