"""Record provider field keys and drop field mappings.

Creates ``integration_tool_fields``: the field keys (never values) each
tool's provider has returned per flow, recorded when sessions complete.

Drops ``client_tool_connections.field_mappings``. Any mappings clients
saved are deleted; flows' own ``outputs`` are unaffected.

Revision ID: 82996fd0ddf2
Revises: c5f3f5bf92e7
Create Date: 2026-10-02 14:15:10.693389

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "82996fd0ddf2"
down_revision: str | Sequence[str] | None = "c5f3f5bf92e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the field key table and drop the mappings column."""
    op.create_table(
        "integration_tool_fields",
        sa.Column("integration_tool_id", sa.Uuid(), nullable=False),
        sa.Column("flow", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_type", sa.String(length=20), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_tool_id"],
            ["integration_tools.id"],
            name="fk_integration_tool_fields_tool",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_tool_fields")),
        sa.UniqueConstraint(
            "integration_tool_id",
            "flow",
            "key",
            name="uq_integration_tool_fields_tool_flow_key",
        ),
    )
    op.drop_column("client_tool_connections", "field_mappings")


def downgrade() -> None:
    """Restore an empty mappings column and drop the field key table.

    Mappings deleted by the upgrade are not restored.
    """
    # Same steps as when the column was first added: MySQL allows no
    # literal default on JSON, so fill existing rows before NOT NULL.
    op.add_column(
        "client_tool_connections",
        sa.Column("field_mappings", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE client_tool_connections SET field_mappings = '{}'")
    op.alter_column(
        "client_tool_connections",
        "field_mappings",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.drop_table("integration_tool_fields")
