"""ORM models for integration tools and the types they serve."""

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin
from app.features.integration_types.models import IntegrationType

TOOL_CODE_MAX_LENGTH = 50
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
