"""Add logo_path to integration tools.

Holds the object key of the tool's logo in the logo store (S3, or a
local folder in development); listings turn it into a full link.
Nullable, since existing tools have no logo.

Revision ID: 5b2d7e1c9a40
Revises: 6826c2876281
Create Date: 2026-10-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b2d7e1c9a40"
down_revision: str | Sequence[str] | None = "6826c2876281"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column."""
    op.add_column(
        "integration_tools",
        sa.Column("logo_path", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    """Drop the column; the logo files themselves are left in the store."""
    op.drop_column("integration_tools", "logo_path")
