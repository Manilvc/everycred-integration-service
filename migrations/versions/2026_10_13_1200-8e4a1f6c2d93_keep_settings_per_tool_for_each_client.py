"""Keep settings per tool for each client.

A client may use several tools for one integration type, and each
provider names its environments differently (SurePass ``sandbox``,
Microsoft ``prod``). Settings therefore move from the client's type
configuration to its row for the tool in ``client_tool_connections``.

Upgrade copies each configuration's settings to the tool it routes to,
creating that row when missing, and empties the type's settings. A tool
routed for two types gets both sets merged.

Revision ID: 8e4a1f6c2d93
Revises: 5b2d7e1c9a40
Create Date: 2026-10-13 12:00:00.000000

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e4a1f6c2d93"
down_revision: str | Sequence[str] | None = "5b2d7e1c9a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

client_integration_configs = sa.table(
    "client_integration_configs",
    sa.column("id", sa.Uuid()),
    sa.column("client_id", sa.Uuid()),
    sa.column("integration_tool_id", sa.Uuid()),
    sa.column("settings", sa.JSON()),
)
client_tool_connections = sa.table(
    "client_tool_connections",
    sa.column("id", sa.Uuid()),
    sa.column("client_id", sa.Uuid()),
    sa.column("integration_tool_id", sa.Uuid()),
    sa.column("is_enabled", sa.Boolean()),
    sa.column("status", sa.String()),
    sa.column("settings", sa.JSON()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade() -> None:
    """Add the column, then move routed settings onto their tools."""
    # JSON columns cannot have a server default on MySQL, so the column
    # is filled before it becomes NOT NULL.
    op.add_column(
        "client_tool_connections",
        sa.Column("settings", sa.JSON(), nullable=True),
    )
    bind = op.get_bind()
    bind.execute(sa.update(client_tool_connections).values(settings={}))
    op.alter_column(
        "client_tool_connections",
        "settings",
        existing_type=sa.JSON(),
        nullable=False,
    )

    settings_by_tool: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
    routed = bind.execute(
        sa.select(
            client_integration_configs.c.client_id,
            client_integration_configs.c.integration_tool_id,
            client_integration_configs.c.settings,
        ).where(client_integration_configs.c.integration_tool_id.is_not(None))
    )
    for client_id, tool_id, settings in routed:
        if settings:
            settings_by_tool.setdefault((client_id, tool_id), {}).update(
                settings
            )

    # Stored as naive UTC, matching what UTCDateTime writes.
    now = datetime.now(UTC).replace(tzinfo=None)
    for (client_id, tool_id), settings in settings_by_tool.items():
        existing = bind.scalar(
            sa.select(client_tool_connections.c.id).where(
                client_tool_connections.c.client_id == client_id,
                client_tool_connections.c.integration_tool_id == tool_id,
            )
        )
        if existing is None:
            # The admin routed the type to this tool, so it starts on.
            bind.execute(
                sa.insert(client_tool_connections).values(
                    id=uuid.uuid4(),
                    client_id=client_id,
                    integration_tool_id=tool_id,
                    is_enabled=True,
                    status="not_tested",
                    settings=settings,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            bind.execute(
                sa.update(client_tool_connections)
                .where(client_tool_connections.c.id == existing)
                .values(settings=settings, updated_at=now)
            )
    bind.execute(
        sa.update(client_integration_configs)
        .where(client_integration_configs.c.integration_tool_id.is_not(None))
        .values(settings={})
    )


def downgrade() -> None:
    """Copy each routed tool's settings back to its type, then drop."""
    bind = op.get_bind()
    routed = bind.execute(
        sa.select(
            client_integration_configs.c.id,
            client_tool_connections.c.settings,
        ).join(
            client_tool_connections,
            sa.and_(
                client_tool_connections.c.client_id
                == client_integration_configs.c.client_id,
                client_tool_connections.c.integration_tool_id
                == client_integration_configs.c.integration_tool_id,
            ),
        )
    )
    for config_id, settings in routed.all():
        if settings:
            bind.execute(
                sa.update(client_integration_configs)
                .where(client_integration_configs.c.id == config_id)
                .values(settings=settings)
            )
    op.drop_column("client_tool_connections", "settings")
