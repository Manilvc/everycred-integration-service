"""Store integration tool logos and build the links that show them.

Logos are public images. The database keeps only an object key such as
``integration-tools/logos/surepass/3f1c....png``; listings turn it into
``LOGO_PUBLIC_BASE_URL/<key>`` with :func:`public_url`. Two stores share
one interface: S3 for deployed environments, and a local folder served
at ``/media`` for development.

Uploaded data is checked here, not trusted from the request: only PNG,
JPEG, and WebP up to :data:`MAX_LOGO_BYTES`, recognised by their file
signature rather than by any declared type.
"""

import base64
import binascii
import logging
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, status
from starlette.concurrency import run_in_threadpool

from app.core.config import (
    LogoStorageBackend,
    Settings,
    SettingsDep,
    get_settings,
)
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

MAX_LOGO_BYTES = 1024 * 1024
# Base64 grows data by a third; allow for that plus a data URI prefix.
MAX_LOGO_TEXT_LENGTH = MAX_LOGO_BYTES * 4 // 3 + 100
# Logos are stored under a new key on every upload, so they can be
# cached for good.
LOGO_CACHE_CONTROL = "public, max-age=31536000, immutable"
_DATA_URI = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,", re.I)


class InvalidLogoError(ValueError):
    """Raised when uploaded logo data is not an accepted image."""


class MediaStoreError(AppError):
    """Raised when a logo could not be stored or deleted."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "media_store_unavailable"

    def __init__(self) -> None:
        super().__init__("The logo could not be stored. Try again later.")


@dataclass(frozen=True, slots=True)
class LogoImage:
    """A decoded, checked logo ready to store."""

    content: bytes
    content_type: str
    extension: str


def decode_logo(text: str) -> LogoImage:
    """Decode base64 (optionally a ``data:`` URI) into a checked image.

    Raises:
        InvalidLogoError: Not base64, empty, too large, or not a PNG,
            JPEG, or WebP file.
    """
    payload = text.strip()
    match = _DATA_URI.match(payload)
    if match:
        payload = payload[match.end() :]
    if len(payload) > MAX_LOGO_TEXT_LENGTH:
        raise InvalidLogoError("the logo must be at most 1 MB")
    try:
        content = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidLogoError("the logo must be base64-encoded") from exc
    if not content:
        raise InvalidLogoError("the logo is empty")
    if len(content) > MAX_LOGO_BYTES:
        raise InvalidLogoError("the logo must be at most 1 MB")
    image = _identify(content)
    if image is None:
        raise InvalidLogoError("the logo must be a PNG, JPEG, or WebP image")
    if match and match.group("mime").lower() not in _ACCEPTED_MIME_TYPES:
        raise InvalidLogoError("the logo must be a PNG, JPEG, or WebP image")
    return image


_ACCEPTED_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


def _identify(content: bytes) -> LogoImage | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return LogoImage(content, "image/png", "png")
    if content.startswith(b"\xff\xd8\xff"):
        return LogoImage(content, "image/jpeg", "jpg")
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return LogoImage(content, "image/webp", "webp")
    return None


def new_logo_key(prefix: str, tool_code: str, extension: str) -> str:
    """Return a fresh object key; a new key per upload defeats stale caches."""
    return (
        f"{prefix.strip('/')}/{tool_code}/{secrets.token_hex(16)}.{extension}"
    )


def public_url(
    key: str | None, settings: Settings | None = None
) -> str | None:
    """Return the link the frontend shows for a stored logo key."""
    if not key:
        return None
    base = (settings or get_settings()).logo_public_base_url.rstrip("/")
    return f"{base}/{key}"


class MediaStore(Protocol):
    """Operations every logo store provides."""

    async def save(self, key: str, image: LogoImage) -> None:
        """Store ``image`` under ``key``."""
        ...

    async def delete(self, key: str) -> None:
        """Remove ``key``; removing a missing key is not an error."""
        ...


@lru_cache(maxsize=4)
def _s3_client(region: str) -> Any:
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=20,
        ),
    )


class S3MediaStore:
    """Logos in an S3 bucket; public reads come from its bucket policy."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    async def save(self, key: str, image: LogoImage) -> None:
        """Upload ``image`` with its content type and long caching."""
        try:
            await run_in_threadpool(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=image.content,
                ContentType=image.content_type,
                CacheControl=LOGO_CACHE_CONTROL,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "Uploading logo %s failed: %s", key, type(exc).__name__
            )
            raise MediaStoreError from exc

    async def delete(self, key: str) -> None:
        """Delete the object; S3 treats a missing key as success."""
        try:
            await run_in_threadpool(
                self.client.delete_object, Bucket=self.bucket, Key=key
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning(
                "Deleting logo %s failed: %s", key, type(exc).__name__
            )
            raise MediaStoreError from exc


class LocalMediaStore:
    """Logos in a local folder served at /media; development only."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Keys are built by the service, but never write outside root.
        if not path.is_relative_to(self.root.resolve()):
            raise MediaStoreError
        return path

    async def save(self, key: str, image: LogoImage) -> None:
        """Write the file, creating folders as needed."""
        path = self._path(key)
        try:
            await run_in_threadpool(
                path.parent.mkdir, parents=True, exist_ok=True
            )
            await run_in_threadpool(path.write_bytes, image.content)
        except OSError as exc:
            logger.error("Writing logo %s failed: %s", key, type(exc).__name__)
            raise MediaStoreError from exc

    async def delete(self, key: str) -> None:
        """Remove the file if it exists."""
        try:
            await run_in_threadpool(self._path(key).unlink, missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Deleting logo %s failed: %s", key, type(exc).__name__
            )
            raise MediaStoreError from exc


def build_media_store(settings: Settings) -> MediaStore:
    """Return the configured logo store."""
    if settings.logo_storage_backend is LogoStorageBackend.S3:
        # Settings validation guarantees both are set here.
        return S3MediaStore(
            _s3_client(settings.aws_region or ""),
            settings.logo_s3_bucket or "",
        )
    return LocalMediaStore(settings.logo_local_dir)


def get_media_store(settings: SettingsDep) -> MediaStore:
    """Return the configured logo store for this request."""
    return build_media_store(settings)


MediaStoreDep = Annotated[MediaStore, Depends(get_media_store)]
