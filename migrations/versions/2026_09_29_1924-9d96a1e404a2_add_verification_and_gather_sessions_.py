"""Add verification and gather sessions and webhooks.

Creates ``integration_sessions`` (one run of a Confirm or Gather flow),
``client_webhooks`` and ``webhook_deliveries`` (signed status events),
and adds ``client_tool_connections.field_mappings`` for per-client
overrides of flow outputs.

Revision ID: 9d96a1e404a2
Revises: e48d40f5d0c3
Create Date: 2026-09-29 19:24:55.508337

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d96a1e404a2"
down_revision: str | Sequence[str] | None = "e48d40f5d0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the session and webhook tables."""
    op.create_table(
        "client_webhooks",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret_reference", sa.String(length=1024), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_webhooks_client",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_webhooks")),
        sa.UniqueConstraint(
            "client_id", name=op.f("uq_client_webhooks_client_id")
        ),
    )
    op.create_table(
        "integration_sessions",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_uuid", sa.Uuid(), nullable=False),
        sa.Column("integration_type_id", sa.Uuid(), nullable=False),
        sa.Column("integration_tool_id", sa.Uuid(), nullable=False),
        sa.Column("flow", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column(
            "current_step", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("awaiting_inputs", sa.JSON(), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column(
            "state_secret_reference", sa.String(length=1024), nullable=True
        ),
        sa.Column(
            "result_secret_reference", sa.String(length=1024), nullable=True
        ),
        sa.Column("result_attributes", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("data_expires_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_integration_sessions_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_tool_id"],
            ["integration_tools.id"],
            name="fk_integration_sessions_tool",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["integration_type_id"],
            ["integration_types.id"],
            name="fk_integration_sessions_type",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_sessions")),
    )
    op.create_index(
        "ix_integration_sessions_client_user",
        "integration_sessions",
        ["client_id", "user_uuid", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_integration_sessions_data_expiry",
        "integration_sessions",
        ["data_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_integration_sessions_status_expiry",
        "integration_sessions",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_webhook_deliveries_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["integration_sessions.id"],
            name="fk_webhook_deliveries_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_client_id"),
        "webhook_deliveries",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_deliveries_session_id"),
        "webhook_deliveries",
        ["session_id"],
        unique=False,
    )
    # Existing rows need a value before the column can be NOT NULL, and
    # MySQL does not allow a literal default on JSON columns.
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


def downgrade() -> None:
    """Drop the session and webhook tables and the mappings column.

    Dropping a table drops its indexes too. Dropping the indexes first
    fails on MySQL where a foreign key relies on them.
    """
    op.drop_column("client_tool_connections", "field_mappings")
    op.drop_table("webhook_deliveries")
    op.drop_table("integration_sessions")
    op.drop_table("client_webhooks")
