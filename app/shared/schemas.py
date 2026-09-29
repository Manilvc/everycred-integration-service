"""Response models shared by every feature.

Keeping the error envelope and pagination wrapper here means all
endpoints return the same shape, and OpenAPI documents it once.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Body of the ``error`` object in an error response."""

    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["The requested resource was not found."])
    request_id: str | None = None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    """Envelope returned for every non-2xx response."""

    error: ErrorDetail


class Page[ItemT](BaseModel):
    """One page of results from a list endpoint."""

    items: list[ItemT]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
