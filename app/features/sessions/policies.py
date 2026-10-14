"""Checks a session request must pass before anything is stored.

Kept apart from the service because none of them touch the database or
a provider: input formats from the flow's ``input_specs``, the freshness
of the relayed consent, and the redirect URI for sign-in flows. Also the
shape in which results are kept in the secret store.
"""

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from app.connectors.http.config import FlowConfig
from app.features.sessions.exceptions import (
    ConsentNotRecentError,
    InputValidationError,
    InvalidRedirectUriError,
    UnknownRequestedAttributesError,
)
from app.features.sessions.schemas import SessionConsent

# The holder agrees on the client's screen moments before the session
# starts; older consent suggests a replayed or stale request.
CONSENT_MAX_AGE = timedelta(minutes=15)
CONSENT_CLOCK_SKEW = timedelta(minutes=5)
# Client setting (per integration type) listing allowed redirect URIs.
REDIRECT_URIS_SETTING = "redirect_uris"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Marks the current layout of a stored result. Attribute names are
# lower_snake_case and cannot start with "_", so this never collides
# with a result stored before fields were added.
RESULT_FORMAT_KEY = "_format"
RESULT_FORMAT_VERSION = 2


def check_input_formats(flow: FlowConfig, inputs: dict[str, Any]) -> None:
    """Enforce the flow's ``input_specs`` before any provider is called.

    Raises:
        InputValidationError: Names of the inputs that do not match.
    """
    invalid: list[str] = []
    for name, value in inputs.items():
        spec = flow.input_specs.get(name)
        if spec is None:
            continue
        if isinstance(value, bool) or not isinstance(value, str | int):
            invalid.append(name)
            continue
        text = str(value)
        is_within_bounds = (
            spec.max_length is None or len(text) <= spec.max_length
        ) and (spec.min_length is None or len(text) >= spec.min_length)
        # Bounds first, so the pattern only ever sees bounded input.
        if not is_within_bounds or (
            spec.pattern is not None and not re.fullmatch(spec.pattern, text)
        ):
            invalid.append(name)
    if invalid:
        raise InputValidationError(sorted(invalid))


def check_consent_recent(consent: SessionConsent, now: datetime) -> None:
    """Reject consent recorded too long ago or in the future.

    Raises:
        ConsentNotRecentError: Outside the accepted window.
    """
    if not (
        now - CONSENT_MAX_AGE
        <= consent.consented_at
        <= now + CONSENT_CLOCK_SKEW
    ):
        raise ConsentNotRecentError(int(CONSENT_MAX_AGE.total_seconds() // 60))


def check_redirect_uri(
    flow: FlowConfig, redirect_uri: str | None, settings: dict[str, Any]
) -> str | None:
    """Return the redirect URI for a redirect flow, after checking it.

    The URI must exactly match one the client registered, so a stolen
    API key alone cannot send holders' authorization codes elsewhere;
    the provider's own redirect URI allow-list is the second check.

    Raises:
        InvalidRedirectUriError: Missing for a flow that sends the
            holder to the provider, sent for another flow, or not
            registered.
    """
    if flow.kind != "redirect":
        if redirect_uri is not None:
            raise InvalidRedirectUriError(
                "This flow does not send the holder to the provider; omit "
                "redirect_uri."
            )
        return None
    if redirect_uri is None:
        raise InvalidRedirectUriError(
            "This flow sends the holder to the provider; send redirect_uri."
        )
    parts = urlsplit(redirect_uri)
    is_local = parts.hostname in LOCAL_HOSTS
    # Settings are saved without validation, so the scheme is checked
    # here: an authorization code must never travel over plain HTTP.
    if parts.fragment or not (
        parts.scheme == "https" or (parts.scheme == "http" and is_local)
    ):
        raise InvalidRedirectUriError(
            "redirect_uri must be an https URL without a fragment."
        )
    allowed = settings.get(REDIRECT_URIS_SETTING)
    if not isinstance(allowed, list) or redirect_uri not in allowed:
        raise InvalidRedirectUriError(
            "redirect_uri is not one of the client's registered "
            f"'{REDIRECT_URIS_SETTING}' for this integration type."
        )
    return redirect_uri


def is_safe_browser_url(url: str) -> bool:
    """Return True if a provider's URL may be shown to the holder's browser.

    Only https (or http on a local host, for development), and never
    with credentials in it, so a broken or hostile provider response
    cannot send the holder to a plain-text or javascript: page.
    """
    parts = urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        return False
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in LOCAL_HOSTS
    )


def check_requested_attributes(
    flow: FlowConfig, requested: list[str] | None
) -> None:
    """Reject requested attributes the flow never outputs.

    Raises:
        UnknownRequestedAttributesError: Names not in ``outputs``.
    """
    if requested is None:
        return
    unknown = sorted(set(requested) - set(flow.outputs))
    if unknown:
        raise UnknownRequestedAttributesError(unknown)


def pack_result(
    attributes: dict[str, Any], fields: dict[str, Any]
) -> dict[str, Any]:
    """Return the secret store payload for a result."""
    return {
        RESULT_FORMAT_KEY: RESULT_FORMAT_VERSION,
        "attributes": attributes,
        "fields": fields,
    }


def unpack_result(
    stored: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(attributes, fields)`` from a stored result.

    Results stored before fields existed are a plain attribute mapping.
    """
    if stored.get(RESULT_FORMAT_KEY) == RESULT_FORMAT_VERSION:
        return dict(stored.get("attributes", {})), dict(
            stored.get("fields", {})
        )
    return dict(stored), {}
