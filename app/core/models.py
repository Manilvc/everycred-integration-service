"""Column types and mixins shared by ORM models.

MySQL ``DATETIME`` columns store no time zone, and drivers hand back
naive datetimes. :class:`UTCDateTime` stores every value as naive UTC
and returns it as an aware UTC datetime, so application code can always
compare against ``datetime.now(UTC)`` without ``TypeError``.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Datetime column that is always UTC on the way in and out."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Convert an aware datetime to naive UTC before saving."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires timezone-aware datetimes")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Mark a naive value loaded from the database as UTC."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class UUIDPrimaryKeyMixin:
    """Adds a random UUID primary key named ``id``.

    Random ids do not reveal how many records exist and can be created
    before the row is inserted.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns set automatically."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )
