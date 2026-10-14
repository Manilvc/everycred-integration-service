"""Record consent and sign-in state on sessions.

Adds the holder's consent record (purpose, notice version, language,
time, the client's reference, withdrawal time), the hash of a pending
sign-in's ``state``, and what the client asked to keep
(``requested_attributes``, ``requested_fields``, ``result_fields``).
None of these columns hold personal data.

Consent columns are nullable because earlier sessions have none; the
API requires consent for every new session.

Revision ID: 3f1c9a7d2b64
Revises: 6826c2876281
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f1c9a7d2b64"
down_revision: str | Sequence[str] | None = "6826c2876281"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

integration_sessions = sa.table(
    "integration_sessions",
    sa.column("result_fields", sa.JSON()),
)


def upgrade() -> None:
    """Add the columns, backfilling ``result_fields`` for old rows."""
    op.add_column(
        "integration_sessions",
        sa.Column("result_fields", sa.JSON(), nullable=True),
    )
    # JSON columns cannot take a portable server default, so fill the
    # existing rows before making the column required.
    op.execute(integration_sessions.update().values(result_fields=[]))
    op.alter_column(
        "integration_sessions",
        "result_fields",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.add_column(
        "integration_sessions",
        sa.Column("requested_attributes", sa.JSON(), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("requested_fields", sa.JSON(), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("oauth_state_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("consent_purpose", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column(
            "consent_notice_version", sa.String(length=64), nullable=True
        ),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("consent_language", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("consented_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("consent_reference", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "integration_sessions",
        sa.Column("consent_withdrawn_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_integration_sessions_client_oauth_state",
        "integration_sessions",
        ["client_id", "oauth_state_hash"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the columns; the consent record is lost."""
    op.drop_index(
        "ix_integration_sessions_client_oauth_state",
        table_name="integration_sessions",
    )
    for column in (
        "consent_withdrawn_at",
        "consent_reference",
        "consented_at",
        "consent_language",
        "consent_notice_version",
        "consent_purpose",
        "oauth_state_hash",
        "requested_fields",
        "requested_attributes",
        "result_fields",
    ):
        op.drop_column("integration_sessions", column)
