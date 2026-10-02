"""Add direction to integration types.

Every type is ``inbound`` (data comes into EveryCRED) or ``outbound``
(EveryCRED acts on or reports to other systems). Confirm, Gather, and
Declare are inbound; Enforcement and Records are outbound. Other types
start as inbound, the column default.

Revision ID: 6826c2876281
Revises: be9555eb2f3b
Create Date: 2026-10-02 15:34:20.512161

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6826c2876281"
down_revision: str | Sequence[str] | None = "be9555eb2f3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTBOUND_CODES = ("enforcement", "records")

integration_types = sa.table(
    "integration_types",
    sa.column("code", sa.String()),
    sa.column("direction", sa.String()),
)


def upgrade() -> None:
    """Add the column and mark the outbound types."""
    op.add_column(
        "integration_types",
        sa.Column(
            "direction",
            sa.String(length=20),
            server_default="inbound",
            nullable=False,
        ),
    )
    op.execute(
        integration_types.update()
        .where(integration_types.c.code.in_(OUTBOUND_CODES))
        .values(direction="outbound")
    )


def downgrade() -> None:
    """Drop the column."""
    op.drop_column("integration_types", "direction")
