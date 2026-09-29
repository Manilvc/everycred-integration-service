"""Storage for credentials and other secrets, referenced from the DB.

Callers store a JSON object and get back an opaque **reference**, which
is all the service's own database keeps. Two backends implement the
same :class:`SecretStore` protocol:

* :class:`AwsSecretsManagerStore` — one AWS Secrets Manager secret per
  value, encrypted with the KMS key in ``SECRETS_KMS_KEY_ID``. The
  reference is the secret's ARN. Required in production.
* :class:`LocalSecretStore` — Fernet-encrypted rows in the
  ``local_secrets`` table, for development and tests where AWS is not
  available. The reference looks like ``local:<uuid>``.

Writes to AWS happen immediately and are not part of the database
transaction, so services create the secret first and delete it again if
their own commit fails (see the services that use this module).
"""

import json
import logging
import uuid
from functools import lru_cache
from typing import Annotated, Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, status
from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from app.core.config import SecretStoreBackend, Settings, SettingsDep
from app.core.database import Base, DbSession
from app.core.encryption import DecryptionError, decrypt_json, encrypt_json
from app.core.exceptions import AppError
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin

logger = logging.getLogger(__name__)

SECRET_REFERENCE_MAX_LENGTH = 1024
LOCAL_REFERENCE_PREFIX = "local:"
_NAME_TAKEN_ERRORS = {"ResourceExistsException", "InvalidRequestException"}


class SecretStoreError(AppError):
    """Raised when a secret cannot be stored or read.

    The client sees a generic message; the AWS error code is logged.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "secret_store_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "Credentials storage is unavailable. Try again later."
        )


class SecretStore(Protocol):
    """Operations every secret store backend provides."""

    async def create(
        self, name: str, value: dict[str, Any], tags: dict[str, str]
    ) -> str:
        """Store ``value`` under ``name`` and return its reference.

        If a secret with that name already exists, its value is
        replaced and the existing reference is returned.
        """
        ...

    async def read(self, reference: str) -> dict[str, Any]:
        """Return the value stored at ``reference``."""
        ...

    async def replace(self, reference: str, value: dict[str, Any]) -> None:
        """Overwrite the value stored at ``reference``."""
        ...

    async def delete(self, reference: str) -> None:
        """Delete the secret. AWS keeps it recoverable for a while."""
        ...


def build_secret_name(prefix: str, *parts: str) -> str:
    """Join a prefix and path parts into a secret name.

    Names contain only ids and codes, never personal data, because AWS
    shows them in the console and in CloudTrail.
    """
    return "/".join([prefix.strip("/"), *parts])


@lru_cache(maxsize=4)
def _secrets_manager_client(region: str) -> Any:
    # boto3 clients are thread-safe and expensive to build, so one per
    # region is shared across requests.
    return boto3.client(
        "secretsmanager",
        region_name=region,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=10,
        ),
    )


class AwsSecretsManagerStore:
    """Secret store backed by AWS Secrets Manager and a KMS key.

    Credentials come from the standard AWS chain (IAM role, environment
    variables, or profile). boto3 is synchronous, so calls run in a
    worker thread.

    The IAM role needs ``secretsmanager:CreateSecret``,
    ``GetSecretValue``, ``PutSecretValue``, ``DescribeSecret``,
    ``RestoreSecret``, ``DeleteSecret``, and ``TagResource`` on
    ``<prefix>/*``, plus ``kms:Encrypt``, ``kms:Decrypt``, and
    ``kms:GenerateDataKey`` on the key.
    """

    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def create(
        self, name: str, value: dict[str, Any], tags: dict[str, str]
    ) -> str:
        """Create the secret, or reuse one that already has this name."""
        secret_name = build_secret_name(
            self._settings.secrets_name_prefix, name
        )
        try:
            response = await run_in_threadpool(
                self._client.create_secret,
                Name=secret_name,
                SecretString=json.dumps(value),
                KmsKeyId=self._settings.secrets_kms_key_id,
                Tags=[{"Key": key, "Value": tag} for key, tag in tags.items()],
            )
            return response["ARN"]
        except ClientError as exc:
            # The name is taken: an earlier request created it
            # (ResourceExistsException), or it was deleted and is still in
            # its recovery window, which AWS reports as
            # InvalidRequestException ("scheduled for deletion").
            if _error_code(exc) not in _NAME_TAKEN_ERRORS:
                raise self._wrap(exc, "create") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, "create") from exc
        return await self._reuse_existing(secret_name, value)

    async def read(self, reference: str) -> dict[str, Any]:
        """Fetch and parse the secret's current value."""
        try:
            response = await run_in_threadpool(
                self._client.get_secret_value, SecretId=reference
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, "read") from exc
        return json.loads(response["SecretString"])

    async def replace(self, reference: str, value: dict[str, Any]) -> None:
        """Store a new version; AWS keeps the previous one as AWSPREVIOUS."""
        try:
            await run_in_threadpool(
                self._client.put_secret_value,
                SecretId=reference,
                SecretString=json.dumps(value),
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, "replace") from exc

    async def delete(self, reference: str) -> None:
        """Schedule deletion after the configured recovery window."""
        try:
            await run_in_threadpool(
                self._client.delete_secret,
                SecretId=reference,
                RecoveryWindowInDays=(
                    self._settings.secret_recovery_window_days
                ),
            )
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return
            raise self._wrap(exc, "delete") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, "delete") from exc

    async def _reuse_existing(
        self, secret_name: str, value: dict[str, Any]
    ) -> str:
        try:
            # Raises ResourceNotFoundException when the InvalidRequest
            # was about something else; that is re-raised as a store
            # error below rather than hidden.
            description = await run_in_threadpool(
                self._client.describe_secret, SecretId=secret_name
            )
            if description.get("DeletedDate"):
                await run_in_threadpool(
                    self._client.restore_secret, SecretId=secret_name
                )
            await run_in_threadpool(
                self._client.put_secret_value,
                SecretId=secret_name,
                SecretString=json.dumps(value),
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._wrap(exc, "create") from exc
        return description["ARN"]

    @staticmethod
    def _wrap(exc: Exception, action: str) -> SecretStoreError:
        # Only the error code is logged; AWS messages can include the
        # secret name, which is fine, but never the value.
        logger.error(
            "Secrets Manager %s failed: %s",
            action,
            _error_code(exc) if isinstance(exc, ClientError) else type(exc),
        )
        return SecretStoreError()


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


class LocalSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A development-only secret kept encrypted in the database.

    Attributes:
        name: Same naming scheme as AWS secret names; unique.
        ciphertext: Fernet token of the JSON value.
    """

    __tablename__ = "local_secrets"

    name: Mapped[str] = mapped_column(String(512), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)


class LocalSecretStore:
    """Secret store for development and tests.

    Values are encrypted with ``CONNECTION_ENCRYPTION_KEYS`` and stored
    in the same database session as the caller's changes, so they are
    committed or rolled back together.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create(
        self, name: str, value: dict[str, Any], tags: dict[str, str]
    ) -> str:
        """Store the value, reusing the row if the name already exists."""
        secret_name = build_secret_name(
            self._settings.secrets_name_prefix, name
        )
        secret = await self._session.scalar(
            select(LocalSecret).where(LocalSecret.name == secret_name)
        )
        if secret is None:
            secret = LocalSecret(name=secret_name)
            self._session.add(secret)
        secret.ciphertext = encrypt_json(value, self._settings)
        await self._session.flush()
        return f"{LOCAL_REFERENCE_PREFIX}{secret.id}"

    async def read(self, reference: str) -> dict[str, Any]:
        """Decrypt and return the stored value."""
        secret = await self._get(reference)
        try:
            return decrypt_json(secret.ciphertext, self._settings)
        except DecryptionError as exc:
            logger.error("Local secret %s could not be decrypted", reference)
            raise SecretStoreError from exc

    async def replace(self, reference: str, value: dict[str, Any]) -> None:
        """Overwrite the stored value."""
        secret = await self._get(reference)
        secret.ciphertext = encrypt_json(value, self._settings)

    async def delete(self, reference: str) -> None:
        """Remove the row; missing references are ignored."""
        try:
            secret = await self._get(reference)
        except SecretStoreError:
            return
        await self._session.delete(secret)

    async def _get(self, reference: str) -> LocalSecret:
        secret = None
        if reference.startswith(LOCAL_REFERENCE_PREFIX):
            try:
                secret_id = uuid.UUID(
                    reference.removeprefix(LOCAL_REFERENCE_PREFIX)
                )
            except ValueError:
                secret_id = None
            if secret_id is not None:
                secret = await self._session.get(LocalSecret, secret_id)
        if secret is None:
            logger.error("Local secret %s does not exist", reference)
            raise SecretStoreError
        return secret


def get_secret_store(session: DbSession, settings: SettingsDep) -> SecretStore:
    """Return the configured secret store for this request."""
    if settings.secret_store_backend is SecretStoreBackend.AWS:
        # Settings validation guarantees the region is set here.
        client = _secrets_manager_client(settings.aws_region or "")
        return AwsSecretsManagerStore(client, settings)
    return LocalSecretStore(session, settings)


SecretStoreDep = Annotated[SecretStore, Depends(get_secret_store)]
