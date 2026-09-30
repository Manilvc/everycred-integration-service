"""ORM model for verification and gather sessions."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.models import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin
from app.core.secret_store import SECRET_REFERENCE_MAX_LENGTH
from app.features.integration_tools.models import IntegrationTool
from app.features.integration_types.models import IntegrationType

FAILURE_MESSAGE_MAX_LENGTH = 500


class SessionPurpose(StrEnum):
    """What a session is for, taken from its flow."""

    VERIFICATION = "verification"
    GATHER = "gather"


class SessionStatus(StrEnum):
    """Lifecycle of a session.

    ``completed`` means the provider gave an answer (see ``outcome``);
    ``failed`` means no answer could be obtained, for example because the
    provider was unreachable.
    """

    AWAITING_INPUT = "awaiting_input"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (SessionStatus.AWAITING_INPUT, SessionStatus.PROCESSING)


class SessionOutcome(StrEnum):
    """Result of a completed session."""

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    GATHERED = "gathered"


class IntegrationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One run of a verification or gather flow for one client user.

    Holder inputs (document numbers, OTPs) and the returned attributes
    are personal data, so neither is stored here: inputs live in the
    secret store only while the session is active, and results only
    until ``data_expires_at``.

    Attributes:
        client_id: Client project the user belongs to.
        user_uuid: The client's id for the user.
        integration_type_id: Type the session runs under.
        integration_tool_id: Tool that serves it.
        flow: Flow name from the tool's configuration.
        purpose: ``verification`` or ``gather``.
        status: Where the session is in its lifecycle.
        outcome: Result once completed.
        current_step: Index of the next step to run.
        awaiting_inputs: Input names needed before the next step.
        reference: The client's own reference, e.g. an invite id.
        state_secret_reference: Collected inputs and captured values,
            while the session is active.
        result_secret_reference: Returned attributes, until purged.
        result_attributes: Names of the returned attributes.
        failure_code: Machine-readable reason for failure.
        failure_message: Safe, human-readable reason.
        expires_at: An unfinished session expires at this time.
        completed_at: When the session finished.
        data_expires_at: When the stored result is deleted.
    """

    __tablename__ = "integration_sessions"
    __table_args__ = (
        Index(
            "ix_integration_sessions_client_user",
            "client_id",
            "user_uuid",
            "created_at",
        ),
        Index("ix_integration_sessions_status_expiry", "status", "expires_at"),
        Index("ix_integration_sessions_data_expiry", "data_expires_at"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_integration_sessions_client",
        ),
        nullable=False,
    )
    user_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    integration_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_types.id",
            ondelete="RESTRICT",
            name="fk_integration_sessions_type",
        ),
        nullable=False,
    )
    integration_tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_tools.id",
            ondelete="RESTRICT",
            name="fk_integration_sessions_tool",
        ),
        nullable=False,
    )
    flow: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(20))
    current_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    awaiting_inputs: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(128))
    state_secret_reference: Mapped[str | None] = mapped_column(
        String(SECRET_REFERENCE_MAX_LENGTH)
    )
    result_secret_reference: Mapped[str | None] = mapped_column(
        String(SECRET_REFERENCE_MAX_LENGTH)
    )
    result_attributes: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(
        String(FAILURE_MESSAGE_MAX_LENGTH)
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    data_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Loaded with the session (selectin), since responses and webhook
    # payloads always name the type and tool.
    integration_type: Mapped[IntegrationType] = relationship(lazy="selectin")
    integration_tool: Mapped[IntegrationTool] = relationship(lazy="selectin")

    @property
    def is_active(self) -> bool:
        """True while the session can still make progress."""
        return self.status in ACTIVE_STATUSES
