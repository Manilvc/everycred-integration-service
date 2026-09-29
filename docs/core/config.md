# `app/core/config.py`

> Last updated: 2026-09-28

## Purpose

Single source of configuration. Every value the service reads from the
environment is declared on `Settings`, validated at startup, and
accessed through `get_settings()`. A missing or malformed variable stops
the process at boot instead of failing on the first request that needs
it.

## Public API

### `Settings`

A `pydantic_settings.BaseSettings` subclass. Field names map to
environment variables case-insensitively (`database_url` ←
`DATABASE_URL`). A `.env` file in the working directory is read for
local development; real environment variables win over it.

### `get_settings() -> Settings`

Returns the cached settings instance.

```python
from app.core.config import get_settings

settings = get_settings()
if settings.is_production:
    ...
```

### `SettingsDep`

`Annotated[Settings, Depends(get_settings)]` for routes and dependency
providers. Tests can replace it with `app.dependency_overrides`.

### `Environment`

`StrEnum` of `local`, `development`, `staging`, `production`.
`Settings.is_production` compares against it.

## How it works

1. **Declaration** — each field has a type and, where sensible, a
   default and bounds (`Field(ge=1)`), so Pydantic rejects values such
   as a pool size of `0`.
2. **Secrets** — `database_url`, `jwt_secret_key`, and
   `super_admin_bootstrap_token` are `SecretStr`. Its `repr` and `str`
   are masked, so logging the settings object does not leak the
   password. Call `.get_secret_value()` only at the point of use
   (`create_database_engine`).
3. **Caching** — `@lru_cache` on `get_settings()` parses the
   environment once per process. The cache also means tests must call
   `get_settings.cache_clear()` after changing environment variables.
4. **List values** — `cors_allowed_origins` is parsed as JSON, so the
   variable is written as `["https://a.example.com"]`.

## Configuration

| Env var                  | Default                         | Effect                                   |
|--------------------------|---------------------------------|------------------------------------------|
| `SERVICE_NAME`           | `everycred-integration-service` | API title, User-Agent for outbound calls |
| `SERVICE_VERSION`        | `0.1.0`                         | API version, User-Agent                  |
| `ENVIRONMENT`            | `local`                         | Controls production-only behaviour       |
| `LOG_LEVEL`              | `INFO`                          | Root log level                           |
| `LOG_JSON`               | `true`                          | JSON vs text log output                  |
| `ENABLE_DOCS`            | `false`                         | Serve `/docs` and `/openapi.json` (never in production) |
| `CORS_ALLOWED_ORIGINS`   | `[]`                            | Origins allowed by CORS; empty disables CORS middleware |
| `DATABASE_URL`           | required                        | `mysql+aiomysql://…?charset=utf8mb4` URL |
| `DATABASE_POOL_SIZE`     | `5`                             | Persistent connections per process       |
| `DATABASE_MAX_OVERFLOW`  | `10`                            | Extra connections allowed under load     |
| `DATABASE_ECHO`          | `false`                         | Log every SQL statement (local only)     |
| `DATABASE_POOL_RECYCLE_SECONDS` | `1800`                   | Replace pooled connections older than this |
| `HTTP_TIMEOUT_SECONDS`   | `10.0`                          | Timeout for outbound HTTP calls          |
| `HTTP_MAX_CONNECTIONS`   | `100`                           | Outbound connection pool size            |
| `JWT_SECRET_KEY`         | required, ≥ 32 chars            | Signs access tokens                      |
| `JWT_ALGORITHM`          | `HS256`                         | `HS256`, `HS384`, or `HS512`             |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30`                       | Access token lifetime (1–1440)           |
| `SUPER_ADMIN_BOOTSTRAP_TOKEN` | unset, ≥ 32 chars          | Allows registering the first super admin |
| `LOGIN_MAX_FAILED_ATTEMPTS` | `5`                          | Wrong passwords before lockout           |
| `LOGIN_LOCKOUT_MINUTES`  | `15`                            | Lockout duration                         |
| `API_KEY_HASH_SECRET`    | required, ≥ 32 chars            | HMAC key for stored client API key hashes |
| `CONNECTION_ENCRYPTION_KEYS` | required                    | Fernet keys for connection parameters; first encrypts |
| `CONNECTOR_TIMEOUT_SECONDS` | `30`                         | Maximum time a connector may run (≤ 300) |
| `SECRET_STORE_BACKEND`   | `local`                         | `aws` (Secrets Manager + KMS) or `local`; must be `aws` in production |
| `AWS_REGION`             | unset                           | Required with `aws` |
| `SECRETS_KMS_KEY_ID`     | unset                           | KMS key ARN, id, or alias; required with `aws` |
| `SECRETS_NAME_PREFIX`    | `everycred/integration-service` | Prefix of every secret name |
| `SECRET_RECOVERY_WINDOW_DAYS` | `7`                        | Days a deleted secret stays recoverable (7-30) |

## Failure modes

| Situation                          | Behaviour                                         |
|------------------------------------|---------------------------------------------------|
| Required variable missing          | `ValidationError` at import of `app.main`; process exits |
| Value out of bounds or wrong type  | Same as above, naming the field                    |

## Changing this module

- Add new settings as typed fields with defaults where a safe default
  exists. Anything secret must be `SecretStr`.
- Add every new variable to `.env.example` and the table above.
- Group feature-specific settings with a common prefix
  (`KYC_PROVIDER_TIMEOUT_SECONDS`) so they are easy to find.

## Tests

`tests/conftest.py` sets `DATABASE_URL`, `JWT_SECRET_KEY`, and the
bootstrap token before the app is imported.
