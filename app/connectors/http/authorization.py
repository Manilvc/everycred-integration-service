"""OpenID Connect authorization code flow with PKCE, for session flows.

Builds the URL the holder's browser opens, then exchanges the code the
provider sends back for an ID token and returns its validated claims.

The ID token comes straight from the token endpoint over TLS, which
OpenID Connect Core 1.0 section 3.1.3.7 allows in place of checking its
signature; the issuer, audience, expiry, and nonce are still checked.
Access and refresh tokens are never kept or returned.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.connectors.base import ConnectorError
from app.connectors.http.config import OAuth2AuthorizationCode
from app.connectors.http.templates import render, url_encode_values

logger = logging.getLogger(__name__)

# Tolerated clock difference with the identity provider.
CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """One-time values binding a redirect to its session.

    Attributes:
        state: Sent to the provider and echoed back; identifies the
            session on return. Only its hash is stored in the database.
        nonce: Must reappear in the ID token, defeating token replay.
        code_verifier: PKCE secret; kept in the secret store until the
            code is exchanged.
    """

    state: str
    nonce: str
    code_verifier: str

    @classmethod
    def new(cls) -> "AuthorizationRequest":
        """Generate fresh random values."""
        return cls(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
        )

    @property
    def code_challenge(self) -> str:
        """S256 PKCE challenge for :attr:`code_verifier` (RFC 7636)."""
        digest = hashlib.sha256(self.code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def hash_state(state: str) -> str:
    """Return the value stored to look a session up by its ``state``."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def build_authorization_url(
    auth: OAuth2AuthorizationCode,
    values: dict[str, Any],
    request: AuthorizationRequest,
    redirect_uri: str,
) -> str:
    """Return the provider URL the holder's browser should open.

    Raises:
        MissingParametersError: A credential or setting is missing.
    """
    base_url = render(auth.authorize_url, url_encode_values(values))
    query = {
        **auth.extra_params,
        "response_type": "code",
        "client_id": str(render(auth.client_id, values)),
        "redirect_uri": redirect_uri,
        "scope": auth.scope,
        "state": request.state,
        "nonce": request.nonce,
        "code_challenge": request.code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{base_url}?{urlencode(query)}"


@dataclass(frozen=True, slots=True)
class SignInResult:
    """What a redeemed code yields.

    Attributes:
        claims: The ID token's validated claims.
        access_token: The user's access token, only when the flow runs
            its steps as the user; never logged or stored.
    """

    claims: dict[str, Any]
    access_token: str | None = field(default=None, repr=False)


async def exchange_code(
    http_client: httpx.AsyncClient,
    auth: OAuth2AuthorizationCode,
    values: dict[str, Any],
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    nonce: str,
    timeout_seconds: float,
) -> SignInResult:
    """Redeem ``code``; return the ID token's validated claims.

    The access token is kept only when ``auth.steps_as_user`` is set.

    Raises:
        MissingParametersError: A credential or setting is missing.
        ConnectorError: The exchange failed or the ID token is not
            valid for this request. Messages never include the
            provider's response body or any token.
    """
    token_url = render(auth.token_url, url_encode_values(values))
    client_id = str(render(auth.client_id, values))
    client_secret = str(render(auth.client_secret, values))
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    basic_auth: tuple[str, str] | None = None
    if auth.client_auth == "basic":
        basic_auth = (client_id, client_secret)
    else:
        form["client_id"] = client_id
        form["client_secret"] = client_secret

    try:
        response = await http_client.post(
            token_url, data=form, auth=basic_auth, timeout=timeout_seconds
        )
    except httpx.TimeoutException as exc:
        raise ConnectorError(
            "The identity provider did not respond in time."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Code exchange failed: %s", type(exc).__name__)
        raise ConnectorError(
            "The identity provider could not be reached."
        ) from exc

    if response.status_code in (400, 401, 403):
        # Expired, reused, or tampered codes all land here.
        raise ConnectorError("The identity provider rejected the sign-in.")
    if not response.is_success:
        raise ConnectorError(
            f"The identity provider returned HTTP {response.status_code}."
        )
    try:
        tokens = response.json()
        id_token = tokens["id_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ConnectorError(
            "The identity provider returned no ID token."
        ) from exc
    access_token = None
    if auth.steps_as_user:
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ConnectorError(
                "The identity provider returned no access token."
            )

    expected_issuer = (
        str(render(auth.issuer, url_encode_values(values)))
        if auth.issuer
        else None
    )
    claims = validate_id_token(
        str(id_token),
        client_id=client_id,
        nonce=nonce,
        issuer=expected_issuer,
    )
    return SignInResult(claims=claims, access_token=access_token)


def validate_id_token(
    id_token: str, *, client_id: str, nonce: str, issuer: str | None
) -> dict[str, Any]:
    """Check an ID token received directly from the token endpoint.

    Raises:
        ConnectorError: A required claim is missing or wrong.
    """
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError as exc:
        raise ConnectorError("The ID token could not be read.") from exc

    audiences = claims.get("aud")
    if isinstance(audiences, str):
        audiences = [audiences]
    if not isinstance(audiences, list) or client_id not in audiences:
        raise ConnectorError("The ID token was issued for another client.")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise ConnectorError("The ID token was issued for another client.")
    if issuer is not None and claims.get("iss") != issuer:
        raise ConnectorError("The ID token comes from an unexpected issuer.")
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int | float) or (
        expires_at + CLOCK_SKEW_SECONDS < time.time()
    ):
        raise ConnectorError("The ID token has expired.")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ConnectorError("The ID token does not match this sign-in.")
    return claims
