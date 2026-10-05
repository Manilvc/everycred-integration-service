"""Request and response models for resolving catalogue ids."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

# Bounds one request; a caller resolving a single page of records never
# needs more distinct ids than this per kind.
MAX_IDS_PER_KIND = 500


class CatalogResolveRequest(BaseModel):
    """Ids a client stored and now needs display data for.

    Attributes:
        type_ids: Integration type ids (what EveryCRED calls source
            roles).
        tool_ids: Integration tool ids (source systems).
        field_ids: Tool field ids (field mappings).
    """

    model_config = ConfigDict(extra="forbid")

    type_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_IDS_PER_KIND
    )
    tool_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_IDS_PER_KIND
    )
    field_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_IDS_PER_KIND
    )


class ResolvedType(BaseModel):
    """Display data of one integration type."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool


class ResolvedTool(BaseModel):
    """Display data of one integration tool."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class ResolvedField(BaseModel):
    """Display data of one tool field.

    Attributes:
        key: Dot path of the field in the provider's response data,
            e.g. ``full_name`` or ``address.zip``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_tool_id: uuid.UUID
    flow: str
    key: str
    label: str | None


class UnresolvedIds(BaseModel):
    """Requested ids with no match the client may see."""

    type_ids: list[uuid.UUID]
    tool_ids: list[uuid.UUID]
    field_ids: list[uuid.UUID]


class CatalogResolveResponse(BaseModel):
    """Display data for every requested id that resolved.

    Attributes:
        not_found: Ids that matched nothing, e.g. a field an admin has
            since removed or another client's own field. Reported per
            id so one stale reference never fails the whole lookup.
    """

    types: list[ResolvedType]
    tools: list[ResolvedTool]
    fields: list[ResolvedField]
    not_found: UnresolvedIds
