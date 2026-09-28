"""Password hashing and access token handling.

Passwords are hashed with Argon2id through :mod:`pwdlib`. Hashing is
deliberately slow (tens of milliseconds), so the async helpers run it in
a worker thread to keep the event loop free for other requests.

Access tokens are signed JWTs. Every token carries an issuer, audience,
expiry, and a ``role`` claim; :func:`decode_access_token` rejects tokens
that are missing any of them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.models import utc_now

TOKEN_AUDIENCE = "everycred-integration-service"  # noqa: S105 - not a secret
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "role", "jti"]

_password_hasher = PasswordHash((Argon2Hasher(),))


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    """Outcome of comparing a password with a stored hash.

    Attributes:
        is_valid: Whether the password matched.
        updated_hash: A fresh hash to store when the hashing parameters
            have changed since the old one was created, otherwise None.
    """

    is_valid: bool
    updated_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A signed access token and when it stops being valid."""

    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified contents of an access token."""

    subject: str
    role: str
    token_id: str
    expires_at: datetime


async def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password``."""
    return await run_in_threadpool(_password_hasher.hash, password)


async def verify_password(password: str, password_hash: str) -> PasswordCheck:
    """Check ``password`` against ``password_hash`` in constant time."""
    is_valid, updated_hash = await run_in_threadpool(
        _password_hasher.verify_and_update, password, password_hash
    )
    return PasswordCheck(is_valid=is_valid, updated_hash=updated_hash)


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return _password_hasher.hash(uuid.uuid4().hex)


async def burn_password_check_time(password: str) -> None:
    """Spend the same time as a real check when the account is unknown.

    Without this, a login for an unknown email returns noticeably faster
    than one with a wrong password, which lets an attacker find out
    which emails have accounts.
    """
    await run_in_threadpool(
        _password_hasher.verify, password, _dummy_password_hash()
    )


def create_access_token(
    subject: str, role: str, settings: Settings
) -> AccessToken:
    """Sign a short-lived access token for ``subject``.

    Args:
        subject: Stable id of the authenticated account.
        role: Role the token grants, checked by route dependencies.
        settings: Source of the signing key, algorithm, and lifetime.
    """
    issued_at = utc_now()
    expires_at = issued_at + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iss": settings.service_name,
        "aud": TOKEN_AUDIENCE,
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return AccessToken(token=token, expires_at=expires_at)


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    """Verify ``token`` and return its claims.

    Raises:
        AuthenticationError: The token is malformed, expired, signed
            with another key, or missing a required claim. The reason is
            not exposed to the caller.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            # Pinning the algorithm list blocks "alg: none" and
            # algorithm-confusion attacks.
            algorithms=[settings.jwt_algorithm],
            audience=TOKEN_AUDIENCE,
            issuer=settings.service_name,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    return TokenClaims(
        subject=claims["sub"],
        role=claims["role"],
        token_id=claims["jti"],
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )
