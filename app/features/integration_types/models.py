"""ORM model for integration types."""

from sqlalchemy import Boolean, Integer, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

CODE_MAX_LENGTH = 50
NAME_MAX_LENGTH = 100


class IntegrationType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A category of integration the service offers, such as "Confirm".

    Types are data, not code: adding a row makes it appear in the
    listing API without a deployment.

    Attributes:
        code: Stable machine identifier (``confirm``) that clients and
            other tables reference. Unique; do not rename once in use.
        name: Human-readable label shown in the admin UI.
        description: Optional longer explanation of the type.
        is_active: Inactive types are hidden from the default listing.
        display_order: Lower numbers are listed first.
    """

    __tablename__ = "integration_types"

    code: Mapped[str] = mapped_column(
        String(CODE_MAX_LENGTH), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
