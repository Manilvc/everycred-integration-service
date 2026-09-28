"""Async SQLAlchemy engine, session factory, and ORM base class.

The engine and session factory are created once in the application
lifespan (see :mod:`app.main`) and exposed to routes through the
:data:`DbSession` dependency. Nothing here connects at import time, so
importing models or running tests does not need a live database.

Sessions are not committed automatically. Services call
``await session.commit()`` when their unit of work succeeds, which
keeps the commit before the response is sent and makes the transaction
boundary visible in the code that owns it.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings

# Deterministic constraint names let Alembic autogenerate migrations
# that can later be downgraded by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the service."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine from settings without opening a connection."""
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        # Drop connections the database closed while they sat idle.
        pool_pre_ping=True,
        pool_recycle=settings.database_pool_recycle_seconds,
        echo=settings.database_echo,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to ``engine``."""
    # expire_on_commit=False keeps loaded attributes usable after commit,
    # which avoids implicit lazy loads (and errors) in async code.
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_database(engine: AsyncEngine) -> None:
    """Run a trivial query, raising if the database is unreachable."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def get_db_engine(request: Request) -> AsyncEngine:
    """Return the engine created during application startup."""
    return request.state.db_engine


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session for one request and close it afterwards.

    Leaving the ``async with`` block rolls back anything the service did
    not commit.
    """
    session_factory: async_sessionmaker[AsyncSession] = (
        request.state.session_factory
    )
    async with session_factory() as session:
        yield session


DbEngine = Annotated[AsyncEngine, Depends(get_db_engine)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
