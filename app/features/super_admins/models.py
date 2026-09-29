"""ORM model for super admin accounts."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin

# RFC 5321 limits a usable address to 254 characters.
EMAIL_MAX_LENGTH = 254
FULL_NAME_MAX_LENGTH = 150


class SuperAdmin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform operator with unrestricted access to the service.

    Attributes:
        email: Login identifier, stored lower-cased and unique.
        full_name: Display name shown in audit trails.
        password_hash: Argon2id hash; the plain password is never stored.
        is_active: Inactive accounts cannot log in or use old tokens.
        failed_login_attempts: Consecutive wrong passwords since the
            last successful login or lockout.
        locked_until: Logins are refused until this time, if set.
        last_login_at: Time of the most recent successful login.
        created_by_id: Super admin who registered this account, or None
            for the bootstrap account.
    """

    __tablename__ = "super_admins"

    email: Mapped[str] = mapped_column(
        String(EMAIL_MAX_LENGTH), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(
        String(FULL_NAME_MAX_LENGTH), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("super_admins.id", ondelete="SET NULL")
    )

    def is_locked(self, now: datetime) -> bool:
        """Return True if logins are currently refused for this account."""
        return self.locked_until is not None and self.locked_until > now
