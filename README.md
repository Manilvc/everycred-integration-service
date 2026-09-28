# everycred-integration-service
EveryCRED Integration Service is a standalone FastAPI microservice that centralizes all external identity and verification integrations for the EveryCRED platform. It resolves the tenant from request context using the same tenant registry pattern as the Issuer API, selects the KYC provider dynamically based on tenant region and configuration, and exposes a uniform API for initiating verification, receiving provider webhooks, and fetching verification status. Provider credentials are loaded per tenant from AWS Secrets Manager, verification outcomes and consent evidence are recorded for audit, and new providers can be added as adapters without changing the core flow. Deploying it independently keeps KYC and provider changes from blocking Issuer API releases.

## Requirements

- Python 3.12+ (the repo pins 3.13 in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- MySQL 8 or MariaDB 10.4+ (local development uses XAMPP's MariaDB)

## Getting started

```bash
uv sync                      # create .venv and install dependencies
cp .env.example .env         # then fill in DATABASE_URL and the secrets
uv run alembic upgrade head  # create or update the database schema
uv run fastapi dev           # http://localhost:8000, auto-reload
```

Create the first super admin once, using the bootstrap token from
`.env` (see [docs/features/super_admins.md](docs/features/super_admins.md)).

With `ENABLE_DOCS=true`, interactive API docs are served at `/docs`
(never in production).

## Common commands

| Task             | Command                              |
|------------------|--------------------------------------|
| Run tests        | `uv run pytest`                      |
| Lint             | `uv run ruff check .`                |
| Format           | `uv run ruff format .`               |
| Production start | `uv run fastapi run --workers 4`     |
| New migration    | `uv run alembic revision --autogenerate -m "<change>"` |
| Apply migrations | `uv run alembic upgrade head`        |

## Documentation

Architecture, the request lifecycle, and how to add a feature are in
[docs/README.md](docs/README.md). Each core module and feature has its
own page under `docs/core/` and `docs/features/`.
