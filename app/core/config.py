"""Service configuration loaded from environment variables.

Every setting the service reads lives on :class:`Settings`. Other modules
call :func:`get_settings` instead of reading ``os.environ`` directly, so
configuration stays in one place and can be overridden in tests.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from cryptography.fernet import Fernet
from fastapi import Depends
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    """Deployment environments the service can run in."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SecretStoreBackend(StrEnum):
    """Where credentials and user connection inputs are stored."""

    AWS = "aws"
    LOCAL = "local"


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

    # Comma-separated Fernet keys. The first encrypts; all of them are
    # tried when decrypting, so a new key can be put in front and the
    # old one kept until existing data has been re-encrypted.
    connection_encryption_keys: SecretStr
    connector_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # Where credentials and user connection inputs are kept. "aws" uses
    # Secrets Manager, encrypting each secret with SECRETS_KMS_KEY_ID;
    # the database stores only the secret's ARN. "local" keeps
    # Fernet-encrypted values in the local_secrets table and exists for
    # development and tests only.
    secret_store_backend: SecretStoreBackend = SecretStoreBackend.LOCAL
    aws_region: str | None = None
    secrets_kms_key_id: str | None = None
    secrets_name_prefix: str = Field(
        default="everycred/integration-service",
        pattern=r"^[A-Za-z0-9/_+=.@-]{1,200}$",
    )
    # Deleted secrets stay recoverable for this long (AWS allows 7-30).
    secret_recovery_window_days: int = Field(default=7, ge=7, le=30)

    # Verification and gather sessions. A session not finished within the
    # TTL expires (its inputs, e.g. an OTP, are deleted). Results are kept
    # encrypted for the retention window, then deleted automatically.
    session_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    session_data_retention_days: int = Field(default=7, ge=1, le=90)

    # Webhooks telling clients a session finished. Private and loopback
    # addresses are refused unless explicitly allowed, so a client cannot
    # make this service call internal systems.
    webhook_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    webhook_max_attempts: int = Field(default=8, ge=1, le=20)
    webhook_allow_private_networks: bool = False

    # Background worker: webhook delivery, session expiry, data purge.
    worker_interval_seconds: float = Field(default=5.0, gt=0, le=300)

    @field_validator("connection_encryption_keys")
    @classmethod
    def check_encryption_keys(cls, keys: SecretStr) -> SecretStr:
        """Fail at startup if any configured key is not a Fernet key."""
        key_list = [
            key.strip()
            for key in keys.get_secret_value().split(",")
            if key.strip()
        ]
        if not key_list:
            raise ValueError("at least one encryption key is required")
        for key in key_list:
            try:
                Fernet(key)
            except ValueError as exc:
                # The key itself is never echoed into the error.
                raise ValueError(
                    "each key must be a urlsafe base64-encoded 32-byte "
                    "Fernet key"
                ) from exc
        return keys

    @model_validator(mode="after")
    def check_secret_store(self) -> "Settings":
        """Refuse configurations that would put secrets in the wrong place.

        Production must use AWS; the local store keeps ciphertext in the
        service's own database, which is exactly what the AWS store
        avoids.
        """
        if (
            self.environment is Environment.PRODUCTION
            and self.secret_store_backend is not SecretStoreBackend.AWS
        ):
            raise ValueError(
                "SECRET_STORE_BACKEND must be 'aws' in production"
            )
        if self.secret_store_backend is SecretStoreBackend.AWS and not (
            self.aws_region and self.secrets_kms_key_id
        ):
            raise ValueError(
                "AWS_REGION and SECRETS_KMS_KEY_ID are required when "
                "SECRET_STORE_BACKEND is 'aws'"
            )
        return self

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
