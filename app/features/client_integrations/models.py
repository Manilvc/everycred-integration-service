"""ORM model for a client's connection to one integration tool."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin

TEST_MESSAGE_MAX_LENGTH = 500


class ToolConnectionStatus(StrEnum):
    """Result of the most recent Test connection.

    ``configured`` means the credentials are stored but the tool offers
    no provider call to check them; ``connected`` means a test call to
    the provider succeeded.
    """

    NOT_TESTED = "not_tested"
    CONFIGURED = "configured"
    CONNECTED = "connected"
    FAILED = "failed"


class ClientToolConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A client's own switch and test status for one tool.

    Credentials live in ``client_tool_credentials`` (a secret store
    reference); this row records whether the client has the tool turned
    on and what the last test found.

    Attributes:
        client_id: Client the connection belongs to.
        integration_tool_id: Tool being connected.
        is_enabled: When False, the client's users cannot run anything
            through this tool.
        status: Outcome of the last Test connection.
        last_tested_at: When Test connection last ran.
        last_test_message: Safe summary of the last test.
    """

    __tablename__ = "client_tool_connections"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "integration_tool_id",
            name="uq_client_tool_connections_client_tool",
        ),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_client_tool_connections_client",
        ),
        nullable=False,
    )
    integration_tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_tools.id",
            ondelete="RESTRICT",
            name="fk_client_tool_connections_tool",
        ),
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=ToolConnectionStatus.NOT_TESTED, nullable=False
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_test_message: Mapped[str | None] = mapped_column(
        String(TEST_MESSAGE_MAX_LENGTH)
    )
