"""Service configuration loaded from environment variables.

Every setting the service reads lives on :class:`Settings`. Other modules
call :func:`get_settings` instead of reading ``os.environ`` directly, so
configuration stays in one place and can be overridden in tests.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    """Deployment environments the service can run in."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Runtime settings for the integration service.

    Values are read from environment variables (case-insensitive) and,
    for local development, from a ``.env`` file in the working
    directory. Real environment variables take precedence over ``.env``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "everycred-integration-service"
    service_version: str = "0.1.0"
    environment: Environment = Environment.LOCAL

    log_level: LogLevel = "INFO"
    log_json: bool = True

    enable_docs: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_echo: bool = False
    # MySQL closes idle connections after ``wait_timeout`` (8 hours by
    # default); recycling earlier avoids "server has gone away" errors.
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)

    http_timeout_seconds: float = Field(default=10.0, gt=0)
    http_max_connections: int = Field(default=100, ge=1)

    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    # Needed only to register the first super admin. Leave unset once
    # bootstrapping is done so the path is closed entirely.
    super_admin_bootstrap_token: SecretStr | None = Field(
        default=None, min_length=32
    )
    login_max_failed_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)

    # Keys API keys' stored HMAC. Changing it invalidates every issued
    # key, so rotate it only together with reissuing all client keys.
    api_key_hash_secret: SecretStr = Field(min_length=32)

    @property
    def is_production(self) -> bool:
        """Return True when running in the production environment."""
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    The result is cached, so the environment is parsed once. Tests that
    change environment variables should call
    ``get_settings.cache_clear()`` afterwards.
    """
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
