"""Create the user_integration_connections table.

Revision ID: d5a8114493b4
Revises: a5e740c50a94
Create Date: 2026-09-28 17:21:54.306776

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5a8114493b4"
down_revision: str | Sequence[str] | None = "a5e740c50a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the connections table and its indexes."""
    op.create_table(
        "user_integration_connections",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_uuid", sa.Uuid(), nullable=False),
        sa.Column("integration_type_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_parameters", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("connection_details", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_user_integration_connections_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_type_id"],
            ["integration_types.id"],
            name="fk_user_integration_connections_integration_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_user_integration_connections")
        ),
        sa.UniqueConstraint(
            "client_id",
            "user_uuid",
            "integration_type_id",
            name="uq_user_integration_connections_user_type",
        ),
    )
    op.create_index(
        "ix_user_integration_connections_client_user",
        "user_integration_connections",
        ["client_id", "user_uuid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_integration_connections_integration_type_id"),
        "user_integration_connections",
        ["integration_type_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the connections table; its indexes go with it."""
    op.drop_table("user_integration_connections")
