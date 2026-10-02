"""ORM model for integration types."""

from enum import StrEnum

from sqlalchemy import Boolean, Integer, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

CODE_MAX_LENGTH = 50
NAME_MAX_LENGTH = 100


class IntegrationDirection(StrEnum):
    """Which way data moves between EveryCRED and the integration.

    ``inbound``: data comes into EveryCRED, e.g. identity checks
    (Confirm), systems of record (Gather), holder input (Declare).
    ``outbound``: EveryCRED acts on or reports to other systems, e.g.
    physical access (Enforcement) and audit (Records).
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"


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
        direction: ``inbound`` or ``outbound``; see
            :class:`IntegrationDirection`.
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
    # The server default keeps rows inserted directly in SQL valid;
    # set it to outbound for outbound types.
    direction: Mapped[str] = mapped_column(
        String(20),
        default=IntegrationDirection.INBOUND,
        server_default=IntegrationDirection.INBOUND.value,
        nullable=False,
    )
