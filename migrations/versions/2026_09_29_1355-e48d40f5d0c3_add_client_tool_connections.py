"""Add client tool connections.

Revision ID: e48d40f5d0c3
Revises: 087d594969e4
Create Date: 2026-09-29 13:55:39.478982

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e48d40f5d0c3"
down_revision: str | Sequence[str] | None = "087d594969e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table holding each client's tool switch and test status."""
    op.create_table(
        "client_tool_connections",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("integration_tool_id", sa.Uuid(), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_message", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_tool_connections_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_tool_id"],
            ["integration_tools.id"],
            name="fk_client_tool_connections_tool",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_tool_connections")),
        sa.UniqueConstraint(
            "client_id",
            "integration_tool_id",
            name="uq_client_tool_connections_client_tool",
        ),
    )
    op.create_index(
        op.f("ix_client_tool_connections_integration_tool_id"),
        "client_tool_connections",
        ["integration_tool_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the table; its indexes go with it."""
    op.drop_table("client_tool_connections")
