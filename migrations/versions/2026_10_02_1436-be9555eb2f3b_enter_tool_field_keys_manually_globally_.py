"""Enter tool field keys manually, globally or per client.

``integration_tool_fields`` now holds keys a super admin enters: global
ones (``client_id`` empty) and per-client ones, each with an optional
``label``. Keys are no longer recorded automatically, so the recorded
rows are deleted and ``last_seen_at`` is dropped.

The new index is created before the old unique key is dropped: on
MySQL that key also serves the foreign key to ``integration_tools``,
which refuses to lose its index.

Revision ID: be9555eb2f3b
Revises: 82996fd0ddf2
Create Date: 2026-10-02 14:36:37.225355

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be9555eb2f3b"
down_revision: str | Sequence[str] | None = "82996fd0ddf2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "integration_tool_fields"


def upgrade() -> None:
    """Switch the table from recorded keys to entered keys."""
    op.execute(f"DELETE FROM {TABLE}")  # noqa: S608 - constant table name
    op.add_column(TABLE, sa.Column("client_id", sa.Uuid(), nullable=True))
    op.add_column(
        TABLE, sa.Column("label", sa.String(length=100), nullable=True)
    )
    op.create_index(
        "ix_integration_tool_fields_tool_client",
        TABLE,
        ["integration_tool_id", "client_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_integration_tool_fields_scope_flow_key",
        TABLE,
        ["integration_tool_id", "client_id", "flow", "key"],
    )
    op.create_foreign_key(
        "fk_integration_tool_fields_client",
        TABLE,
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_integration_tool_fields_tool_flow_key", TABLE, type_="unique"
    )
    op.drop_column(TABLE, "last_seen_at")


def downgrade() -> None:
    """Return to one global list per tool with ``last_seen_at``.

    Client-specific fields are deleted: the old table had no place for
    them, and its unique key would reject the same key for two clients.
    """
    op.execute(
        f"DELETE FROM {TABLE} WHERE client_id IS NOT NULL"  # noqa: S608
    )
    op.add_column(
        TABLE, sa.Column("last_seen_at", sa.DateTime(), nullable=True)
    )
    op.execute(f"UPDATE {TABLE} SET last_seen_at = created_at")  # noqa: S608
    op.alter_column(
        TABLE, "last_seen_at", existing_type=sa.DateTime(), nullable=False
    )
    op.create_unique_constraint(
        "uq_integration_tool_fields_tool_flow_key",
        TABLE,
        ["integration_tool_id", "flow", "key"],
    )
    op.drop_constraint(
        "fk_integration_tool_fields_client", TABLE, type_="foreignkey"
    )
    op.drop_constraint(
        "uq_integration_tool_fields_scope_flow_key", TABLE, type_="unique"
    )
    op.drop_index("ix_integration_tool_fields_tool_client", table_name=TABLE)
    op.drop_column(TABLE, "label")
    op.drop_column(TABLE, "client_id")
