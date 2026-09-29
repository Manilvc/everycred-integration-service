"""ORM models for client webhooks and their delivery attempts."""

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
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin
from app.core.secret_store import SECRET_REFERENCE_MAX_LENGTH

WEBHOOK_URL_MAX_LENGTH = 2048
DELIVERY_ERROR_MAX_LENGTH = 300


class DeliveryStatus(StrEnum):
    """State of one webhook event's delivery."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class ClientWebhook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Where a client wants to be told that its sessions finished.

    Attributes:
        client_id: Client the endpoint belongs to (one per client).
        url: HTTPS endpoint receiving events.
        secret_reference: Secret store reference to the signing secret.
        is_active: Inactive endpoints receive nothing.
    """

    __tablename__ = "client_webhooks"

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_client_webhooks_client",
        ),
        unique=True,
        nullable=False,
    )
    url: Mapped[str] = mapped_column(
        String(WEBHOOK_URL_MAX_LENGTH), nullable=False
    )
    secret_reference: Mapped[str] = mapped_column(
        String(SECRET_REFERENCE_MAX_LENGTH), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One event waiting for, or done with, delivery.

    The payload carries identifiers and status only, never personal
    data; the client fetches results with its API key.

    Attributes:
        client_id: Client to notify.
        session_id: Session the event is about, if any.
        event: Event type, e.g. ``session.completed``.
        payload: JSON body sent to the endpoint.
        status: ``pending``, ``delivered``, or ``failed`` (gave up).
        attempts: Delivery attempts so far.
        next_attempt_at: When the worker should try next.
        last_status_code: HTTP status of the last attempt, if any.
        last_error: Safe description of the last failure.
        delivered_at: When the endpoint accepted the event.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "clients.id",
            ondelete="CASCADE",
            name="fk_webhook_deliveries_client",
        ),
        nullable=False,
        index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_sessions.id",
            ondelete="CASCADE",
            name="fk_webhook_deliveries_session",
        ),
        index=True,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=DeliveryStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(
        String(DELIVERY_ERROR_MAX_LENGTH)
    )
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
