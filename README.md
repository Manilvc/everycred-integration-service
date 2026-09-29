# everycred-integration-service
EveryCRED Integration Service is a standalone FastAPI microservice that centralizes all external identity and verification integrations for the EveryCRED platform. It resolves the tenant from request context using the same tenant registry pattern as the Issuer API, selects the KYC provider dynamically based on tenant region and configuration, and exposes a uniform API for initiating verification, receiving provider webhooks, and fetching verification status. Provider credentials are loaded per tenant from AWS Secrets Manager, verification outcomes and consent evidence are recorded for audit, and new providers can be added as adapters without changing the core flow. Deploying it independently keeps KYC and provider changes from blocking Issuer API releases.

## Requirements

- Python 3.12+ (the repo pins 3.13 in `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- MySQL 8 or MariaDB 10.4+ (local development uses XAMPP's MariaDB)

## Getting started

1. Install dependencies into `.venv`:

   ```bash
   uv sync
   ```

2. Create your local settings file and fill in `DATABASE_URL` and the
   secrets (each variable is explained in `.env.example`):

   ```bash
   cp .env.example .env               # macOS / Linux / Git Bash
   Copy-Item .env.example .env        # Windows PowerShell
   ```

3. Make sure MySQL is running (for local development, start MySQL in
   the XAMPP Control Panel), then create or update the schema:

   ```bash
   uv run alembic upgrade head
   ```

4. Start the server (see below).

5. Create the first super admin once, using the bootstrap token from
   `.env` (see [docs/features/super_admins.md](docs/features/super_admins.md)).

## Running the server

The app entrypoint (`app.main:app`) is set in `pyproject.toml`, so no
path argument is needed.

### Development

```bash
uv run fastapi dev
```

- Serves on http://127.0.0.1:8000 and reloads when code changes.
- Use another port with `uv run fastapi dev --port 8001`.
- Stop with `Ctrl+C`.

### Production

```bash
uv run fastapi run --workers 4
```

- Listens on `0.0.0.0:8000` with no auto-reload.
- Set `--workers` to roughly the number of CPU cores. Each worker opens
  its own database pool (`DATABASE_POOL_SIZE` + `DATABASE_MAX_OVERFLOW`
  connections), so size MySQL's `max_connections` accordingly.
- Set `ENVIRONMENT=production` and `LOG_JSON=true`, and run it behind a
  reverse proxy or load balancer that terminates TLS.

### Check it is running

| URL | Expected |
|-----|----------|
| http://127.0.0.1:8000/health/live | `{"status": "ok"}` |
| http://127.0.0.1:8000/health/ready | `200` when MySQL is reachable, `503` otherwise |
| http://127.0.0.1:8000/redoc | ReDoc API reference, grouped by caller |
| http://127.0.0.1:8000/docs | Swagger UI, for trying requests from the browser |
| http://127.0.0.1:8000/openapi.json | Raw OpenAPI 3.1 schema (for client generators or Postman) |

The three documentation URLs exist only when `ENABLE_DOCS=true`, and
never when `ENVIRONMENT=production`. The descriptions shown there live
in `app/api/openapi.py`.

If the server exits at startup with a validation error, a required
variable in `.env` is missing or invalid; the message names the field.

### Docker (deployed environments)

The service is deployed as a container behind the shared backend domain at
`https://api-evrc.viitorcloud.in/integration/`:

```bash
docker compose build
docker compose up -d        # runs migrations, then starts the API on 127.0.0.1:8030
```

Then add the nginx location from `deploy/nginx/integration-subpath.conf`.
Full steps, environment, MySQL and AWS setup: [docs/deployment.md](docs/deployment.md).
Merges to `development` deploy automatically: see [docs/cicd.md](docs/cicd.md).

## Common commands

| Task             | Command                              |
|------------------|--------------------------------------|
| Start (dev)      | `uv run fastapi dev`                 |
| Start (prod)     | `uv run fastapi run --workers 4`     |
| Run tests        | `uv run pytest`                      |
| Lint             | `uv run ruff check .`                |
| Format           | `uv run ruff format .`               |
| New migration    | `uv run alembic revision --autogenerate -m "<change>"` |
| Apply migrations | `uv run alembic upgrade head`        |
| Current revision | `uv run alembic current`             |

## Documentation

Architecture, the request lifecycle, and how to add a feature are in
[docs/README.md](docs/README.md). Each core module and feature has its
own page under `docs/core/` and `docs/features/`.
