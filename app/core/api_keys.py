"""Generation and verification of client API keys.

A key looks like ``eci_3f9a1c07b2de_<43 random characters>``:

* ``eci`` marks it as an EveryCRED integration key, which makes leaked
  keys easy to spot in logs and by secret scanners.
* The 12-character hex **prefix** is stored in plain text and indexed.
  It identifies the key for lookups and is safe to show in the admin UI.
* The random **secret** carries 256 bits of entropy.

Only an HMAC-SHA256 of the full key is stored. Because keys are long and
random, a fast keyed hash is enough; unlike passwords they cannot be
guessed from a dictionary, and the server-side HMAC secret means a
leaked database alone cannot be used to confirm a key.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.core.config import Settings

API_KEY_SCHEME = "eci"
_PREFIX_BYTES = 6
_SECRET_BYTES = 32
PREFIX_LENGTH = _PREFIX_BYTES * 2


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    """A newly created key, before it is stored.

    Attributes:
        plaintext: The full key. Return it to the caller once and never
            store or log it.
        prefix: Lookup identifier saved alongside the hash.
        key_hash: Hex HMAC-SHA256 of ``plaintext`` to store.
    """

    plaintext: str
    prefix: str
    key_hash: str


def hash_api_key(api_key: str, settings: Settings) -> str:
    """Return the hex HMAC-SHA256 of ``api_key``."""
    return hmac.new(
        settings.api_key_hash_secret.get_secret_value().encode(),
        api_key.encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key(settings: Settings) -> GeneratedApiKey:
    """Create a random API key and the values needed to store it."""
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    plaintext = f"{API_KEY_SCHEME}_{prefix}_{secret}"
    return GeneratedApiKey(
        plaintext=plaintext,
        prefix=prefix,
        key_hash=hash_api_key(plaintext, settings),
    )


def extract_api_key_prefix(api_key: str) -> str | None:
    """Return the lookup prefix of ``api_key``, or None if malformed."""
    # maxsplit=2 because the secret part may itself contain underscores.
    parts = api_key.split("_", 2)
    if len(parts) != 3:
        return None
    scheme, prefix, secret = parts
    if scheme != API_KEY_SCHEME or len(prefix) != PREFIX_LENGTH or not secret:
        return None
    return prefix


def is_api_key_valid(
    api_key: str, stored_hash: str, settings: Settings
) -> bool:
    """Compare ``api_key`` with a stored hash in constant time."""
    return hmac.compare_digest(hash_api_key(api_key, settings), stored_hash)
