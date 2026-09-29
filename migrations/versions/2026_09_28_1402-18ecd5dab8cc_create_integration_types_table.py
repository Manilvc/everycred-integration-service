"""Create the integration_types table and seed the initial types.

Revision ID: 18ecd5dab8cc
Revises: 8da3816dcae0
Create Date: 2026-09-28 14:02:05.347885

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "18ecd5dab8cc"
down_revision: str | Sequence[str] | None = "8da3816dcae0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Seeded here so every environment starts with the same catalogue. More
# types can be inserted directly into the table later; they appear in
# the listing API without a code change.
INITIAL_INTEGRATION_TYPES = [
    ("confirm", "Confirm", 10),
    ("gather", "Gather", 20),
    ("enforcement", "Enforcement", 30),
    ("records", "Records", 40),
]


def upgrade() -> None:
    """Create the table and insert the initial integration types."""
    integration_types = op.create_table(
        "integration_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_types")),
        sa.UniqueConstraint("code", name=op.f("uq_integration_types_code")),
    )

    # Stored as naive UTC, matching what UTCDateTime writes.
    seeded_at = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        integration_types,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "is_active": True,
                "display_order": display_order,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
            for code, name, display_order in INITIAL_INTEGRATION_TYPES
        ],
    )


def downgrade() -> None:
    """Drop the table, including any types added after seeding."""
    op.drop_table("integration_types")
