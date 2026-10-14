"""Connect users per tool.

A user may connect to several tools of one integration type (SurePass
and Microsoft Entra ID for Confirm, say). ``user_integration_connections``
gains ``integration_tool_id`` and becomes unique per (client, user,
type, tool) instead of per type.

Existing connections ran through the type's routed tool, so they are
assigned that tool. Connections of a type with no tool keep null and
are taken over by the type's default tool once it has one.

Revision ID: 4c7d2e9b1a58
Revises: 8e4a1f6c2d93
Create Date: 2026-10-14 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c7d2e9b1a58"
down_revision: str | Sequence[str] | None = "8e4a1f6c2d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_integration_connections"
OLD_UNIQUE = "uq_user_integration_connections_user_type"
NEW_UNIQUE = "uq_user_integration_connections_user_tool"

client_integration_configs = sa.table(
    "client_integration_configs",
    sa.column("client_id", sa.Uuid()),
    sa.column("integration_type_id", sa.Uuid()),
    sa.column("integration_tool_id", sa.Uuid()),
)
user_integration_connections = sa.table(
    TABLE,
    sa.column("client_id", sa.Uuid()),
    sa.column("integration_type_id", sa.Uuid()),
    sa.column("integration_tool_id", sa.Uuid()),
)


def upgrade() -> None:
    """Add the tool column, fill it, and widen the unique key."""
    op.add_column(
        TABLE, sa.Column("integration_tool_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        op.f("ix_user_integration_connections_integration_tool_id"),
        TABLE,
        ["integration_tool_id"],
    )
    op.create_foreign_key(
        "fk_user_integration_connections_tool",
        TABLE,
        "integration_tools",
        ["integration_tool_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    routed_tool = (
        sa.select(client_integration_configs.c.integration_tool_id)
        .where(
            client_integration_configs.c.client_id
            == user_integration_connections.c.client_id,
            client_integration_configs.c.integration_type_id
            == user_integration_connections.c.integration_type_id,
        )
        .scalar_subquery()
    )
    op.get_bind().execute(
        sa.update(user_integration_connections).values(
            integration_tool_id=routed_tool
        )
    )

    # The new key is created before the old one is dropped, so the
    # client_id foreign key always has an index to use on MySQL.
    op.create_unique_constraint(
        NEW_UNIQUE,
        TABLE,
        [
            "client_id",
            "user_uuid",
            "integration_type_id",
            "integration_tool_id",
        ],
    )
    op.drop_constraint(OLD_UNIQUE, TABLE, type_="unique")


def downgrade() -> None:
    """Return to one connection per type.

    Fails if a user has connections to two tools of one type; remove
    the extra ones first.
    """
    op.create_unique_constraint(
        OLD_UNIQUE, TABLE, ["client_id", "user_uuid", "integration_type_id"]
    )
    op.drop_constraint(NEW_UNIQUE, TABLE, type_="unique")
    op.drop_constraint(
        "fk_user_integration_connections_tool", TABLE, type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_user_integration_connections_integration_tool_id"),
        table_name=TABLE,
    )
    op.drop_column(TABLE, "integration_tool_id")
