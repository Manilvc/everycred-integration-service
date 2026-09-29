"""Request and response models for super admin endpoints."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.features.super_admins.models import (
    EMAIL_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
)

# NIST SP 800-63B favours length over composition rules. The upper bound
# stops multi-megabyte passwords from being used to burn hashing CPU.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def _normalise_email(email: str) -> str:
    return email.strip().lower()


class SuperAdminRegistration(BaseModel):
    """Details for a new super admin account."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    full_name: str = Field(min_length=1, max_length=FULL_NAME_MAX_LENGTH)
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        repr=False,
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, email: str) -> str:
        """Lower-case the address so uniqueness ignores letter case."""
        return _normalise_email(email)

    @field_validator("full_name")
    @classmethod
    def strip_full_name(cls, full_name: str) -> str:
        """Trim surrounding spaces and reject names that are only spaces."""
        stripped_name = full_name.strip()
        if not stripped_name:
            raise ValueError("full_name must not be blank")
        return stripped_name


class SuperAdminLogin(BaseModel):
    """Credentials submitted to the login endpoint."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(
        min_length=1, max_length=PASSWORD_MAX_LENGTH, repr=False
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, email: str) -> str:
        """Match the normalisation applied at registration."""
        return _normalise_email(email)


class SuperAdminResponse(BaseModel):
    """Super admin account as returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class AccessTokenResponse(BaseModel):
    """Bearer token issued after a successful login."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - scheme name
    expires_in: int = Field(description="Seconds until the token expires.")
    expires_at: datetime
