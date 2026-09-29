"""Alembic environment for the integration service.

The database URL is taken from the application's settings
(``DATABASE_URL``), not from ``alembic.ini``, so migrations always run
against the same database as the app and no credentials live in the
ini file.
"""

import asyncio
from logging.config import fileConfig
from typing import Literal

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Autogenerate only sees tables whose models have been imported. Add an
# import here for every new feature's models module.
import app.features.client_integrations.models  # noqa: F401
import app.features.clients.models  # noqa: F401
import app.features.integration_tools.models  # noqa: F401
import app.features.integration_types.models  # noqa: F401
import app.features.sessions.models  # noqa: F401
import app.features.super_admins.models  # noqa: F401
import app.features.user_connections.models  # noqa: F401
import app.features.webhooks.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.core.models import UTCDateTime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_url = get_settings().database_url.get_secret_value()


def _render_item(
    type_: str, obj: object, autogen_context: object
) -> str | Literal[False]:
    # Write app-specific column types as their plain SQL type so that
    # migration files never import application code, which may change
    # or be removed after the migration was written.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _configure_context(**options: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_item=_render_item,
        # Detect column type changes (e.g. String(100) -> String(150)),
        # which autogenerate skips by default.
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting (``--sql``)."""
    _configure_context(
        url=database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_on_connection(connection: Connection) -> None:
    _configure_context(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    # NullPool: a migration run is a one-off process, so pooling
    # connections would only delay shutdown.
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations_on_connection)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
