"""ORM models for integration tools and the types they serve."""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin
from app.features.integration_types.models import IntegrationType

TOOL_CODE_MAX_LENGTH = 50
FIELD_KEY_MAX_LENGTH = 255
FIELD_LABEL_MAX_LENGTH = 100
FLOW_NAME_MAX_LENGTH = 64
TOOL_NAME_MAX_LENGTH = 100
PROVIDER_MAX_LENGTH = 100

# Link table: a tool can serve several integration types, and a type can
# be served by several tools. Rows disappear with either side.
integration_tool_types = Table(
    "integration_tool_types",
    Base.metadata,
    Column(
        "integration_tool_id",
        Uuid,
        ForeignKey(
            "integration_tools.id",
            ondelete="CASCADE",
            name="fk_integration_tool_types_tool",
        ),
        primary_key=True,
    ),
    Column(
        "integration_type_id",
        Uuid,
        ForeignKey(
            "integration_types.id",
            ondelete="CASCADE",
            name="fk_integration_tool_types_type",
        ),
        primary_key=True,
        index=True,
    ),
)


class IntegrationTool(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An external tool that can fulfil one or more integration types.

    Attributes:
        code: Stable identifier, also the key its connector registers
            under (``@register_connector("<code>")``). Do not rename
            once clients use it.
        name: Display name.
        provider: Company or product behind the tool.
        description: Optional longer explanation.
        is_active: Inactive tools are hidden from clients and cannot be
            chosen for new configurations.
        display_order: Lower numbers are listed first.
        connector_config: Configuration for the generic HTTP connector
            (see ``app/connectors/http/config.py``), or None for tools
            served by Python code or not wired up yet. Holds no
            secrets; credentials are stored per client.
        integration_types: Types this tool can be used for.
    """

    __tablename__ = "integration_tools"

    code: Mapped[str] = mapped_column(
        String(TOOL_CODE_MAX_LENGTH), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(TOOL_NAME_MAX_LENGTH), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(PROVIDER_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    connector_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # selectin loading fetches the types in one extra query per batch of
    # tools; lazy loading is not allowed in async sessions.
    integration_types: Mapped[list[IntegrationType]] = relationship(
        secondary=integration_tool_types,
        lazy="selectin",
        order_by=IntegrationType.display_order,
    )

    def serves(self, integration_type_id: uuid.UUID) -> bool:
        """Return True if this tool is linked to the given type id."""
        return any(
            integration_type.id == integration_type_id
            for integration_type in self.integration_types
        )


class FieldValueType(StrEnum):
    """JSON type of a field's value in the provider's response."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class IntegrationToolField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A field key a tool's provider returns, entered by a super admin.

    A reference list for client backends: which keys a flow's response
    holds (``full_name``, ``dob``, ``address.zip``...), so they can be
    named consistently. Only keys are stored, never values.

    Global fields (``client_id`` empty) apply to every client; client
    fields add to them for one client only.

    Attributes:
        integration_tool_id: Tool whose provider returns the field.
        client_id: The client the field is for, or None when global.
        flow: Flow whose response holds the field.
        key: Dot path of the field in the provider's response data.
        label: Human-readable name, e.g. "Date of birth".
        value_type: JSON type of the value; see :class:`FieldValueType`.
    """

    __tablename__ = "integration_tool_fields"
    __table_args__ = (
        UniqueConstraint(
            "integration_tool_id",
            "client_id",
            "flow",
            "key",
            name="uq_integration_tool_fields_scope_flow_key",
        ),
        Index(
            "ix_integration_tool_fields_tool_client",
            "integration_tool_id",
            "client_id",
        ),
    )

    integration_tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_tools.id",
            ondelete="CASCADE",
            name="fk_integration_tool_fields_tool",
        ),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_integration_tool_fields_client",
        ),
    )
    flow: Mapped[str] = mapped_column(
        String(FLOW_NAME_MAX_LENGTH), nullable=False
    )
    key: Mapped[str] = mapped_column(
        String(FIELD_KEY_MAX_LENGTH), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(FIELD_LABEL_MAX_LENGTH))
    value_type: Mapped[str] = mapped_column(
        String(20), default=FieldValueType.STRING, nullable=False
    )
