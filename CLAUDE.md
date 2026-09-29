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

## Connectors

- Each row in `integration_tools` gets a connector in
  `app/connectors/<tool_code>.py`, registered with
  `@register_connector("<tool_code>")` and imported in
  `app/connectors/__init__.py`. Follow `docs/connectors.md`.
- Connectors are keyed by tool, not by integration type; the tool is
  the one chosen in the client's `client_integration_configs` row.
- Prefer a `connector_config` (generic HTTP connector) over Python
  code for REST providers; see `docs/connectors.md`.
- Credentials and user inputs go through `app/core/secret_store.py`
  only; the database stores references. Never log or return them.

## Client Integrations screen

- `app/features/client_integrations/` backs the client admin's
  Integrations page (X-API-Key). Card statuses are derived in
  `_ToolState.card_status`; keep them in sync with `CardStatus` and
  `docs/features/client_integrations.md`.

## Auth

- Client projects authenticate with `X-API-Key` through the
  `CurrentClient` dependency (`app/features/clients/dependencies.py`).
  Never log or return a full key except in the one-time issue response.
- Passwords and JWTs go through `app/core/security.py` only.
- Protect super admin routes with the `CurrentSuperAdmin` dependency,
  or `dependencies=[Depends(get_current_super_admin)]` on the router
  when every route needs it.
