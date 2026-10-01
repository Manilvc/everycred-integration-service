"""Add the declare type and the holder wallet app.

Brings the type catalogue in line with the EveryCRED Integrations
screen: a ``declare`` type ("Holder & issuer input") between Gather and
Enforcement, subtitles for the seeded types, and the built-in
``holder-wallet-app`` tool listed under Declare.

Existing rows are only filled in, never overwritten: a description or
display order an admin has changed is kept.

Revision ID: c5f3f5bf92e7
Revises: 9d96a1e404a2
Create Date: 2026-10-01 11:05:18.371402

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f3f5bf92e7"
down_revision: str | Sequence[str] | None = "9d96a1e404a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECLARE_CODE = "declare"
HOLDER_WALLET_APP_CODE = "holder-wallet-app"

# Subtitles from the EveryCRED Integrations screen.
DESCRIPTIONS = {
    "confirm": "Identity & verification",
    "gather": "Systems of record (read-only)",
    DECLARE_CODE: "Holder & issuer input",
    "enforcement": "Physical access systems",
    "records": "Audit & evidence",
}
# (code, seeded order, new order): Declare takes 30, so the two types
# after it move down, unless an admin has already reordered them.
REORDERED = [("enforcement", 30, 40), ("records", 40, 50)]

integration_types = sa.table(
    "integration_types",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("display_order", sa.Integer()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)
integration_tools = sa.table(
    "integration_tools",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("provider", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("display_order", sa.Integer()),
    sa.column("connector_config", sa.JSON()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)
integration_tool_types = sa.table(
    "integration_tool_types",
    sa.column("integration_tool_id", sa.Uuid()),
    sa.column("integration_type_id", sa.Uuid()),
)
client_tool_connections = sa.table(
    "client_tool_connections",
    sa.column("integration_tool_id", sa.Uuid()),
)


def _id_of(table: sa.TableClause, code: str) -> uuid.UUID | None:
    return op.get_bind().scalar(
        sa.select(table.c.id).where(table.c.code == code)
    )


def upgrade() -> None:
    """Add Declare, fill in subtitles, and seed the holder wallet app."""
    # Stored as naive UTC, matching what UTCDateTime writes.
    now = datetime.now(UTC).replace(tzinfo=None)
    bind = op.get_bind()

    for code, seeded_order, new_order in REORDERED:
        bind.execute(
            integration_types.update()
            .where(
                integration_types.c.code == code,
                integration_types.c.display_order == seeded_order,
            )
            .values(display_order=new_order, updated_at=now)
        )

    declare_id = _id_of(integration_types, DECLARE_CODE)
    if declare_id is None:
        declare_id = uuid.uuid4()
        bind.execute(
            integration_types.insert().values(
                id=declare_id,
                code=DECLARE_CODE,
                name="Declare",
                description=DESCRIPTIONS[DECLARE_CODE],
                is_active=True,
                display_order=30,
                created_at=now,
                updated_at=now,
            )
        )

    for code, description in DESCRIPTIONS.items():
        bind.execute(
            integration_types.update()
            .where(
                integration_types.c.code == code,
                integration_types.c.description.is_(None),
            )
            .values(description=description, updated_at=now)
        )

    if _id_of(integration_tools, HOLDER_WALLET_APP_CODE) is None:
        wallet_id = uuid.uuid4()
        bind.execute(
            integration_tools.insert().values(
                id=wallet_id,
                code=HOLDER_WALLET_APP_CODE,
                name="Holder Wallet App",
                provider="EveryCRED",
                description="Wallet Application",
                is_active=True,
                display_order=10,
                created_at=now,
                updated_at=now,
            )
        )
        bind.execute(
            integration_tool_types.insert().values(
                integration_tool_id=wallet_id, integration_type_id=declare_id
            )
        )


def downgrade() -> None:
    """Remove the holder wallet app and the Declare type.

    Fails, instead of deleting client settings, if a client has
    configured Declare or stored credentials for the wallet: those
    references are RESTRICT. Subtitles are left in place.
    """
    bind = op.get_bind()
    wallet_id = _id_of(integration_tools, HOLDER_WALLET_APP_CODE)
    if wallet_id is not None:
        # Only the on/off switch and last test result per client.
        bind.execute(
            client_tool_connections.delete().where(
                client_tool_connections.c.integration_tool_id == wallet_id
            )
        )
        bind.execute(
            integration_tools.delete().where(
                integration_tools.c.id == wallet_id
            )
        )
    bind.execute(
        integration_types.delete().where(
            integration_types.c.code == DECLARE_CODE
        )
    )
    for code, seeded_order, new_order in REORDERED:
        bind.execute(
            integration_types.update()
            .where(
                integration_types.c.code == code,
                integration_types.c.display_order == new_order,
            )
            .values(display_order=seeded_order)
        )
