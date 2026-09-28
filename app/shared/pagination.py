"""Query parameters for paginated list endpoints."""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PaginationParams(BaseModel):
    """Offset-based pagination read from the query string."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


# Use as a route parameter: ``pagination: Pagination``.
Pagination = Annotated[PaginationParams, Query()]
