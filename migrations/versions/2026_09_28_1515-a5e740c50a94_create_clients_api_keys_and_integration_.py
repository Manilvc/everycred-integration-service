"""Create clients, client_api_keys, and client_integration_configs.

Revision ID: a5e740c50a94
Revises: 18ecd5dab8cc
Create Date: 2026-09-28 15:15:07.397060

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5e740c50a94"
down_revision: str | Sequence[str] | None = "18ecd5dab8cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the client tables and their indexes."""
    op.create_table(
        "clients",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["super_admins.id"],
            name=op.f("fk_clients_created_by_id_super_admins"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("code", name=op.f("uq_clients_code")),
    )
    op.create_table(
        "client_api_keys",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_client_api_keys_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["super_admins.id"],
            name=op.f("fk_client_api_keys_created_by_id_super_admins"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_api_keys")),
        sa.UniqueConstraint(
            "key_prefix", name=op.f("uq_client_api_keys_key_prefix")
        ),
    )
    op.create_index(
        op.f("ix_client_api_keys_client_id"),
        "client_api_keys",
        ["client_id"],
        unique=False,
    )
    op.create_table(
        "client_integration_configs",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("integration_type_id", sa.Uuid(), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_client_integration_configs_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_type_id"],
            ["integration_types.id"],
            name="fk_client_integration_configs_integration_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_client_integration_configs")
        ),
        sa.UniqueConstraint(
            "client_id",
            "integration_type_id",
            name="uq_client_integration_configs_client_type",
        ),
    )
    op.create_index(
        op.f("ix_client_integration_configs_integration_type_id"),
        "client_integration_configs",
        ["integration_type_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the client tables, children first.

    Indexes go with their tables; dropping them separately fails on
    MySQL while a foreign key still depends on them.
    """
    op.drop_table("client_integration_configs")
    op.drop_table("client_api_keys")
    op.drop_table("clients")
