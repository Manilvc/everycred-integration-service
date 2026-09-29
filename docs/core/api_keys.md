# `app/core/api_keys.py`

> Last updated: 2026-09-28

## Purpose

Create API keys for client projects and check them on each request,
without ever storing a key in a form that could be used if the database
leaked.

## Public API

### `generate_api_key(settings) -> GeneratedApiKey`

Returns:

- `plaintext` — the full key to hand to the client once;
- `prefix` — 12 hex characters, stored in plain text for lookup;
- `key_hash` — 64-character hex HMAC-SHA256 to store.

### `extract_api_key_prefix(api_key) -> str | None`

Pulls the prefix out of a presented key, or returns `None` if the key
is not in the expected format. Lets callers reject garbage without a
database query.

### `is_api_key_valid(api_key, stored_hash, settings) -> bool`

Recomputes the HMAC and compares it with `hmac.compare_digest`.

### `hash_api_key(api_key, settings) -> str`

The HMAC itself; used by the two functions above.

## Key format

```
eci_3f9a1c07b2de_Yq0m...43 characters...
 │        │              │
 │        │              └─ secret: secrets.token_urlsafe(32), 256 bits
 │        └─ prefix: secrets.token_hex(6), unique, indexed
 └─ scheme: marks the string as an EveryCRED integration key
```

A recognisable scheme lets GitHub secret scanning and log filters spot
leaked keys. The secret may contain `_`, so parsing splits on the first
two underscores only.

## How it works

1. **Issue** — generate prefix and secret with `secrets`, join them,
   HMAC the full string with `API_KEY_HASH_SECRET`, and store only the
   prefix and HMAC.
2. **Verify** — extract the prefix, load the row by prefix (one indexed
   lookup), recompute the HMAC of the presented key, and compare in
   constant time.

### Why HMAC-SHA256 rather than Argon2

Passwords are short and guessable, so they need a slow hash. API keys
carry 256 random bits and cannot be brute-forced, so a fast hash is
safe, and it keeps per-request verification to microseconds. The HMAC
secret adds a second factor: someone with only a database dump cannot
even test whether a candidate key is valid.

## Configuration

| Env var               | Default  | Effect                        |
|-----------------------|----------|-------------------------------|
| `API_KEY_HASH_SECRET` | required, ≥ 32 chars | HMAC key for stored hashes |

Changing `API_KEY_HASH_SECRET` makes every issued key fail. Rotate it
only together with reissuing all client keys.

## Changing this module

- Keep the scheme prefix stable; secret scanners and clients may rely
  on it.
- If keys ever need scopes, store them on the `client_api_keys` row,
  not in the key string.
