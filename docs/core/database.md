# `app/core/database.py`

> Last updated: 2026-09-28

## Purpose

Owns the connection to MySQL (MariaDB locally) through SQLAlchemy 2.x
async with the `aiomysql` driver, and provides the ORM base class for
models. Shared column types and mixins live in
[models](models.md).

## Public API

### `Base`

Declarative base for every ORM model. Its `MetaData` uses
`NAMING_CONVENTION` so constraints get deterministic names
(`pk_super_admins`, `uq_super_admins_email`), which Alembic needs to
generate reversible migrations.

```python
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
```

### `DbSession`

`Annotated[AsyncSession, Depends(get_db_session)]`. Use it in
dependency providers to build repositories:

```python
def get_verification_service(session: DbSession) -> VerificationService:
    return VerificationService(VerificationRepository(session))
```

### `DbEngine`

`Annotated[AsyncEngine, Depends(get_db_engine)]`, for code that needs
the engine itself (the readiness check).

### `create_database_engine(settings)`, `create_session_factory(engine)`

Called by the lifespan in `app.main`. Neither opens a connection.

### `ping_database(engine)`

Runs `SELECT 1`; raises if the database is unreachable.

## How it works

1. **Startup** — `lifespan` builds the engine and session factory and
   yields them as lifespan state. Starlette copies that state onto
   `request.state` for each request.
2. **Per request** — `get_db_session` opens a session from
   `request.state.session_factory` and closes it when the request ends.
3. **Transactions** — sessions are **not** committed automatically. The
   service that owns the unit of work calls `await session.commit()`.
   Closing the session rolls back anything uncommitted, including after
   an exception. Committing in the service keeps the commit before the
   response is sent, so a failed commit is reported to the client
   instead of being lost after a `200`.
4. **Engine options** — `pool_pre_ping=True` discards connections the
   server dropped while idle, and `pool_recycle` replaces connections
   before MySQL's `wait_timeout` closes them. `expire_on_commit=False`
   keeps attributes loaded after commit, avoiding implicit lazy loads,
   which are errors in async SQLAlchemy.
5. **Shutdown** — `engine.dispose()` closes pooled connections.

## Configuration

`DATABASE_URL`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`,
`DATABASE_ECHO`, `DATABASE_POOL_RECYCLE_SECONDS` — see
[config](config.md).

Always include `?charset=utf8mb4` in the URL so names and emails in any
script round-trip correctly.

Total connections per process can reach `pool_size + max_overflow`.
Multiply by worker and replica count when sizing the database's
`max_connections`.

## Failure modes

| Situation                    | Behaviour                                               |
|------------------------------|---------------------------------------------------------|
| Database down at startup     | Service starts; `/health/ready` returns `503`           |
| Database down during request | Driver error reaches the middleware → `500`, logged     |
| Forgotten `commit()`         | Changes silently rolled back — covered by service tests |

## Migrations

Alembic lives in `migrations/`. `migrations/env.py` reads
`DATABASE_URL` from the app settings, so no credentials are stored in
`alembic.ini`.

```bash
uv run alembic revision --autogenerate -m "add verifications table"
uv run alembic upgrade head
uv run alembic check          # fails if models and migrations differ
```

- New feature models must be imported in `migrations/env.py`, or
  autogenerate will not see their tables.
- `env.py` renders `UTCDateTime` as `sa.DateTime()` so migration files
  never import application code.
- Review every generated migration before applying it. Autogenerate
  cannot detect renames; it produces a drop and an add instead.
- MySQL runs DDL outside transactions. A migration that fails halfway
  leaves the schema partly changed, so keep each migration small.

## Changing this module

- Keep all engine options in `create_database_engine` so the app and
  tests build engines the same way.
