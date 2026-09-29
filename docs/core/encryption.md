# `app/core/encryption.py`

> Last updated: 2026-09-28

## Purpose

Encrypt values that must be stored but must not be readable from the
database, currently user connection parameters.

## Public API

### `encrypt_json(value, settings) -> str`

Serialises `value` to compact JSON and returns a Fernet token
(`gAAAAA…`).

### `decrypt_json(ciphertext, settings) -> Any`

Reverses `encrypt_json`. Raises `DecryptionError` if the token was
modified or no configured key can decrypt it.

## How it works

1. **Fernet** combines AES-128-CBC with an HMAC-SHA256 over the
   ciphertext, so a stored value can be neither read nor altered
   without the key. Each token has a random IV, so equal values encrypt
   differently.
2. **MultiFernet** is built from `CONNECTION_ENCRYPTION_KEYS`. The first
   key encrypts; all keys are tried when decrypting.
3. **Caching** — the cipher is built once per distinct key string.

## Configuration

`CONNECTION_ENCRYPTION_KEYS`: one or more Fernet keys, comma-separated.
Every key is validated at startup; an invalid key stops the service
without printing the key.

Generate a key:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Key rotation

1. Generate a new key and put it **first**:
   `CONNECTION_ENCRYPTION_KEYS=<new>,<old>`.
2. Deploy. New writes use the new key; old values still decrypt.
3. Re-save or re-encrypt existing rows, then remove the old key.

Losing every key makes stored parameters permanently unreadable; clients
would have to save them again. Keep keys in the secrets manager, with a
backup.

## Tests

`tests/core/test_encryption.py` — round trip, tampering, rotation, and
startup validation.
