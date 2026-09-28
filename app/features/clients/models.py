"""ORM models for client projects, their API keys, and their settings."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin

CLIENT_CODE_MAX_LENGTH = 50
CLIENT_NAME_MAX_LENGTH = 150
API_KEY_NAME_MAX_LENGTH = 100


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A project that integrates with this service.

    Attributes:
        code: Short unique slug, e.g. ``issuer-portal``.
        name: Display name.
        description: Optional notes about the project.
        is_active: Inactive clients are refused on every API key call.
        created_by_id: Super admin who created the client.
    """

    __tablename__ = "clients"

    code: Mapped[str] = mapped_column(
        String(CLIENT_CODE_MAX_LENGTH), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(CLIENT_NAME_MAX_LENGTH), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("super_admins.id", ondelete="SET NULL")
    )


class ClientApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An API key a client uses to call the service.

    Attributes:
        client_id: Client the key belongs to.
        name: Label such as ``production`` so keys can be told apart.
        key_prefix: Public identifier used to find the key; unique.
        key_hash: HMAC-SHA256 of the full key. The key itself is never
            stored.
        expires_at: Key stops working after this time, if set.
        revoked_at: Key stopped working at this time, if set.
        last_used_at: Approximate time of the last successful use.
        created_by_id: Super admin who issued the key.
    """

    __tablename__ = "client_api_keys"

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(API_KEY_NAME_MAX_LENGTH), nullable=False
    )
    key_prefix: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("super_admins.id", ondelete="SET NULL")
    )

    def is_usable(self, now: datetime) -> bool:
        """Return True if the key is neither revoked nor expired."""
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


class ClientIntegrationConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How one client uses one integration type.

    Attributes:
        client_id: Client the configuration belongs to.
        integration_type_id: Integration type being configured.
        is_enabled: Disabled configurations are hidden from the client.
        settings: Free-form, non-secret options for this integration.
            Provider credentials belong in the secrets manager, not here.
    """

    __tablename__ = "client_integration_configs"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "integration_type_id",
            name="uq_client_integration_configs_client_type",
        ),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT: an integration type still in use must be deactivated,
    # not deleted, so clients' settings are never silently lost. The
    # constraint is named explicitly because the generated name would
    # exceed MySQL's 64-character identifier limit.
    integration_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "integration_types.id",
            ondelete="RESTRICT",
            name="fk_client_integration_configs_integration_type",
        ),
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
