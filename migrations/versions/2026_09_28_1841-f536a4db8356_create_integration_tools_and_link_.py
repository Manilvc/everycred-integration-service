"""Create integration tools and link client configs.

Revision ID: f536a4db8356
Revises: d5a8114493b4
Create Date: 2026-09-28 18:41:58.983848

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f536a4db8356"
down_revision: str | Sequence[str] | None = "d5a8114493b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tool tables and let client configs point at a tool."""
    op.create_table(
        "integration_tools",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "display_order", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_tools")),
        sa.UniqueConstraint("code", name=op.f("uq_integration_tools_code")),
    )
    op.create_table(
        "integration_tool_types",
        sa.Column("integration_tool_id", sa.Uuid(), nullable=False),
        sa.Column("integration_type_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_tool_id"],
            ["integration_tools.id"],
            name="fk_integration_tool_types_tool",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_type_id"],
            ["integration_types.id"],
            name="fk_integration_tool_types_type",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "integration_tool_id",
            "integration_type_id",
            name=op.f("pk_integration_tool_types"),
        ),
    )
    op.create_index(
        op.f("ix_integration_tool_types_integration_type_id"),
        "integration_tool_types",
        ["integration_type_id"],
        unique=False,
    )
    op.add_column(
        "client_integration_configs",
        sa.Column("integration_tool_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_client_integration_configs_integration_tool_id"),
        "client_integration_configs",
        ["integration_tool_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_client_integration_configs_tool",
        "client_integration_configs",
        "integration_tools",
        ["integration_tool_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove tool links from client configs, then the tool tables.

    The link table's index is dropped with the table; dropping it on
    its own fails on MySQL while a foreign key still uses it.
    """
    op.drop_constraint(
        "fk_client_integration_configs_tool",
        "client_integration_configs",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_client_integration_configs_integration_tool_id"),
        table_name="client_integration_configs",
    )
    op.drop_column("client_integration_configs", "integration_tool_id")
    op.drop_table("integration_tool_types")
    op.drop_table("integration_tools")
