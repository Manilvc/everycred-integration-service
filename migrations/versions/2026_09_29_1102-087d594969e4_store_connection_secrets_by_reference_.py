"""Keep connection secrets in the secret store and add operation audit.

Revision ID: 087d594969e4
Revises: f536a4db8356
Create Date: 2026-09-29 11:02:00.764576

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "087d594969e4"
down_revision: str | Sequence[str] | None = "f536a4db8356"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add credential, local secret, and audit tables; reference secrets.

    User connection inputs move from an encrypted column to the secret
    store. They cannot be moved inside a migration (AWS may not be
    reachable), so the upgrade refuses to run while connections exist;
    delete them and have clients save their inputs again.
    """
    _refuse_if_rows("user_integration_connections")
    op.create_table(
        "local_secrets",
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_secrets")),
        sa.UniqueConstraint("name", name=op.f("uq_local_secrets_name")),
    )
    op.create_table(
        "client_tool_credentials",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("integration_tool_id", sa.Uuid(), nullable=False),
        sa.Column("secret_reference", sa.String(length=1024), nullable=False),
        sa.Column("credential_names", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_tool_credentials_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_tool_id"],
            ["integration_tools.id"],
            name="fk_client_tool_credentials_tool",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_tool_credentials")),
        sa.UniqueConstraint(
            "client_id",
            "integration_tool_id",
            name="uq_client_tool_credentials_client_tool",
        ),
    )
    op.create_index(
        op.f("ix_client_tool_credentials_integration_tool_id"),
        "client_tool_credentials",
        ["integration_tool_id"],
        unique=False,
    )
    op.create_table(
        "integration_operation_logs",
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column("user_uuid", sa.Uuid(), nullable=False),
        sa.Column(
            "integration_type_code", sa.String(length=50), nullable=False
        ),
        sa.Column("tool_code", sa.String(length=50), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_integration_operation_logs_client",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_integration_operation_logs")
        ),
    )
    op.create_index(
        "ix_integration_operation_logs_client_user",
        "integration_operation_logs",
        ["client_id", "user_uuid", "created_at"],
        unique=False,
    )
    op.add_column(
        "integration_tools",
        sa.Column("connector_config", sa.JSON(), nullable=True),
    )
    op.add_column(
        "user_integration_connections",
        sa.Column(
            "parameters_secret_reference",
            sa.String(length=1024),
            nullable=False,
        ),
    )
    op.add_column(
        "user_integration_connections",
        sa.Column("parameter_summary", sa.JSON(), nullable=False),
    )
    op.drop_column("user_integration_connections", "encrypted_parameters")


def downgrade() -> None:
    """Undo the upgrade. Refuses while connections exist, like upgrade.

    Indexes go with their tables; dropping them on their own fails on
    MySQL while a foreign key still uses them.
    """
    _refuse_if_rows("user_integration_connections")
    op.add_column(
        "user_integration_connections",
        sa.Column("encrypted_parameters", sa.Text(), nullable=False),
    )
    op.drop_column("user_integration_connections", "parameter_summary")
    op.drop_column(
        "user_integration_connections", "parameters_secret_reference"
    )
    op.drop_column("integration_tools", "connector_config")
    op.drop_table("integration_operation_logs")
    op.drop_table("client_tool_credentials")
    op.drop_table("local_secrets")


def _refuse_if_rows(table_name: str) -> None:
    row_count = (
        op.get_bind()
        .execute(sa.text(f"SELECT COUNT(*) FROM {table_name}"))  # noqa: S608
        .scalar_one()
    )
    if row_count:
        raise RuntimeError(
            f"{table_name} has {row_count} rows whose inputs cannot be "
            "moved automatically. Delete them and have clients save the "
            "inputs again, then rerun this migration."
        )
