# `app/core/security.py`

> Last updated: 2026-09-28

## Purpose

Password hashing and access token signing for every account type in
the service. Features decide *who* may log in; this module decides
*how* passwords and tokens are handled, so there is one implementation
to review and keep up to date.

## Public API

### `hash_password(password) -> str` (async)

Returns an Argon2id hash (`$argon2id$v=19$…`) using pwdlib's
recommended parameters.

### `verify_password(password, password_hash) -> PasswordCheck` (async)

`PasswordCheck.is_valid` says whether the password matched.
`PasswordCheck.updated_hash` is set when the stored hash uses outdated
parameters; callers should save it so hashes upgrade on next login.

### `burn_password_check_time(password)` (async)

Runs a verification against a throwaway hash. Call it on login paths
that fail *before* a real password check (unknown email, locked
account) so every failure takes about the same time.

### `create_access_token(subject, role, settings) -> AccessToken`

Signs a JWT and returns it with its expiry time.

| Claim  | Value                                     |
|--------|-------------------------------------------|
| `sub`  | Account id                                |
| `role` | Role granted, e.g. `super_admin`          |
| `iss`  | `SERVICE_NAME`                            |
| `aud`  | `everycred-integration-service`           |
| `iat`  | Issue time                                |
| `exp`  | `iat + ACCESS_TOKEN_EXPIRE_MINUTES`       |
| `jti`  | Random id, for future revocation lists    |

### `decode_access_token(token, settings) -> TokenClaims`

Verifies signature, expiry, issuer, audience, and that every claim
above is present. Any failure raises `AuthenticationError("Invalid or
expired token.")`; the specific reason is never returned to the
caller.

## How it works

1. **Argon2id** is memory-hard, which makes GPU cracking of leaked
   hashes expensive. It is the first choice in the OWASP Password
   Storage Cheat Sheet.
2. **Thread offloading** — each hash or verify takes tens of
   milliseconds of CPU. Running it with `run_in_threadpool` stops a
   burst of logins from stalling every other request on the event loop.
3. **Dummy hash** — `_dummy_password_hash()` is created on first use
   and cached, with a random password nobody knows.
4. **Algorithm pinning** — `decode_access_token` passes exactly one
   allowed algorithm (`JWT_ALGORITHM`). This blocks `alg: none` tokens
   and tokens that try to switch algorithms.
5. **Required claims** — `options={"require": [...]}` makes PyJWT
   reject tokens missing `exp` or other claims, instead of silently
   treating them as non-expiring.

## Configuration

`JWT_SECRET_KEY` (≥ 32 characters), `JWT_ALGORITHM`,
`ACCESS_TOKEN_EXPIRE_MINUTES` — see [config](config.md).

Rotating `JWT_SECRET_KEY` logs everyone out immediately, because
existing tokens stop verifying.

## Failure modes

| Situation                         | Behaviour                         |
|-----------------------------------|-----------------------------------|
| Expired, forged, or garbled token | `AuthenticationError` → `401`     |
| Stored hash in unknown format     | `pwdlib.exceptions.UnknownHashError` → `500`, logged |

## Changing this module

- Access tokens cannot be revoked before they expire. If that becomes
  a requirement, store `jti` values in a deny list, or add short-lived
  access tokens with refresh tokens.
- To move to asymmetric signing (RS256/EdDSA) so other services can
  verify tokens without the secret, change `create_access_token` and
  `decode_access_token` together and add the key settings.

## Tests

`tests/features/super_admins/test_login.py` covers expired tokens,
wrong signing keys, malformed tokens, and role checks.
