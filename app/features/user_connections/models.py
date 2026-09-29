"""ORM model for per-user integration connections."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import (
    TimestampMixin,
    UTCDateTime,
    UUIDPrimaryKeyMixin,
    utc_now,
)
from app.core.secret_store import SECRET_REFERENCE_MAX_LENGTH

LAST_ERROR_MAX_LENGTH = 500


class ConnectionStatus(StrEnum):
    """Lifecycle of a user's connection to an integration."""

    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"


class UserIntegrationConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One client user's connection to one integration type.

    Attributes:
        client_id: Client project the user belongs to.
        user_uuid: The client's own identifier for the user. This
            service keeps no user records; the pair (client, user) is
            the identity.
        integration_type_id: Integration the user is connected to.
        parameters_secret_reference: Where the user's
            ``{"args": [...], "kwargs": {...}}`` are kept in the secret
            store (a Secrets Manager ARN in deployed environments).
        parameter_summary: ``{"arg_count": n, "kwarg_names": [...]}``,
            kept here so listings never need to read the secret.
        status: Result of the most recent connection attempt.
        connection_details: Non-secret output of the last successful
            connection, such as a provider account reference.
        last_error: Safe description of the last failure.
        last_attempt_at: When a connection was last attempted.
        last_connected_at: When a connection last succeeded.
    """

    __tablename__ = "user_integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "user_uuid",
            "integration_type_id",
            name="uq_user_integration_connections_user_type",
        ),
        Index(
            "ix_user_integration_connections_client_user",
            "client_id",
            "user_uuid",
        ),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_user_integration_connections_client",
        ),
        nullable=False,
    )
    user_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    integration_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_types.id",
            ondelete="RESTRICT",
            name="fk_user_integration_connections_integration_type",
        ),
        nullable=False,
        index=True,
    )
    parameters_secret_reference: Mapped[str] = mapped_column(
        String(SECRET_REFERENCE_MAX_LENGTH), nullable=False
    )
    parameter_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=ConnectionStatus.PENDING, nullable=False
    )
    connection_details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(
        String(LAST_ERROR_MAX_LENGTH)
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class IntegrationOperationLog(UUIDPrimaryKeyMixin, Base):
    """Audit record of one operation a client ran for one of its users.

    Only metadata is kept. Inputs and provider results can contain
    personal data, so neither is stored here.

    Attributes:
        client_id: Client that ran the operation; set to null if the
            client is deleted so the audit trail survives.
        user_uuid: The client's id for the user.
        integration_type_code: Type the operation ran under.
        tool_code: Tool that served it.
        operation: Operation name.
        succeeded: Whether the provider reported success.
        provider_status_code: HTTP status from the provider, if any.
        error_code: This service's error code when the call failed.
        duration_ms: Time spent calling the provider.
        request_id: Matches the ``X-Request-ID`` of the API call.
        created_at: When the operation ran.
    """

    __tablename__ = "integration_operation_logs"
    __table_args__ = (
        Index(
            "ix_integration_operation_logs_client_user",
            "client_id",
            "user_uuid",
            "created_at",
        ),
    )

    client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="SET NULL",
            name="fk_integration_operation_logs_client",
        ),
    )
    user_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    integration_type_code: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    tool_code: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider_status_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
