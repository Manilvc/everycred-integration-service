# everycred-integration-service

FastAPI service for KYC and external identity integrations. Global
Python rules in `~/.claude/CLAUDE.md` apply; this file adds what is
specific to this repo.

## Commands

- `uv run pytest` — tests
- `uv run ruff check . && uv run ruff format .` — lint and format
- `uv run fastapi dev` — local server (entrypoint set in `pyproject.toml`)
- `uv run alembic upgrade head` — apply migrations
- `uv run alembic revision --autogenerate -m "<change>"` — new migration
- `uv run alembic check` — confirm models and migrations match

Use `uv add <package>` to add dependencies; never edit the dependency
list by hand.

## Structure

Read `docs/README.md` before adding code. In short:

- New features go in `app/features/<feature_name>/` and are registered
  in `app/api/router.py`.
- Reuse what `app/core/` provides: `DbSession`, `HttpClient`,
  `AppError` subclasses, `get_settings()`. Do not create new engines,
  HTTP clients, or settings readers.
- Paginated endpoints take `Pagination` and return `Page[Schema]` from
  `app/shared/`.
- Services commit their own transactions (`await session.commit()`).
- Shared resources are provided through lifespan state
  (`request.state`), not module globals.

## Database

- MySQL via `aiomysql`. Local dev uses XAMPP MariaDB 10.4 as root, with
  database `everycred_integration`. The XAMPP `mysql.db` grants table is
  corrupted, so creating scoped users fails there; do not try to repair
  it without asking.
- Models use `UUIDPrimaryKeyMixin`, `TimestampMixin`, and `UTCDateTime`
  from `app/core/models.py`. Always use `utc_now()`, never naive
  datetimes.
- Import new models modules in `migrations/env.py`. Review generated
  migrations; they must not import `app` code.
- Tests use in-memory SQLite through a `get_db_session` override, so
  avoid MySQL-only SQL in models.

## Auth

- Passwords and JWTs go through `app/core/security.py` only.
- Protect super admin routes with the `CurrentSuperAdmin` dependency.
