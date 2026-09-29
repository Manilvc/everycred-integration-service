"""Encryption of sensitive values stored in the database.

Values are serialised to JSON and encrypted with Fernet (AES-128-CBC
with an HMAC-SHA256 signature), so a stored token can be neither read
nor altered without the key. :class:`~cryptography.fernet.MultiFernet`
allows key rotation: the first configured key encrypts, and every key
is tried when decrypting.
"""

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings


class DecryptionError(Exception):
    """Raised when stored ciphertext cannot be decrypted.

    Either the data was tampered with, or it was encrypted with a key
    that is no longer configured.
    """


@lru_cache(maxsize=4)
def _build_cipher(serialised_keys: str) -> MultiFernet:
    keys = [key.strip() for key in serialised_keys.split(",") if key.strip()]
    return MultiFernet([Fernet(key) for key in keys])


def _cipher(settings: Settings) -> MultiFernet:
    return _build_cipher(
        settings.connection_encryption_keys.get_secret_value()
    )


def encrypt_json(value: Any, settings: Settings) -> str:
    """Serialise ``value`` to JSON and return it encrypted."""
    plaintext = json.dumps(value, separators=(",", ":")).encode()
    return _cipher(settings).encrypt(plaintext).decode()


def decrypt_json(ciphertext: str, settings: Settings) -> Any:
    """Decrypt a value produced by :func:`encrypt_json`.

    Raises:
        DecryptionError: The ciphertext is invalid or no configured key
            can decrypt it.
    """
    try:
        plaintext = _cipher(settings).decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise DecryptionError("Stored value could not be decrypted.") from exc
    return json.loads(plaintext)
