# `app/core/exceptions.py`

> Last updated: 2026-09-28

## Purpose

Let services report failures without knowing about HTTP, and make
every error response share one shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "Verification was not found.",
    "request_id": "6f37b4e671f344f99e4a0736491737ea",
    "details": []
  }
}
```

`details` is present only for validation errors.

## Public API

### Error classes

| Class                   | Status | `error_code`             | Use when                                   |
|-------------------------|--------|--------------------------|--------------------------------------------|
| `AppError`              | 400    | `bad_request`            | Base class; generic client error           |
| `AuthenticationError`   | 401    | `authentication_failed`  | Missing or invalid credentials             |
| `PermissionDeniedError` | 403    | `permission_denied`      | Authenticated but not allowed              |
| `NotFoundError`         | 404    | `not_found`              | Resource absent (or not visible to tenant) |
| `ConflictError`         | 409    | `conflict`               | Duplicate or invalid state transition      |
| `ExternalServiceError`  | 502    | `external_service_error` | A KYC or other provider failed             |

Feature errors subclass one of these and override `error_code`:

```python
class VerificationNotFoundError(NotFoundError):
    """Raised when no verification matches the id for this tenant."""

    error_code = "verification_not_found"
```

The message is sent to the client verbatim, so it must be safe to show.

### Response headers

`AppError.headers` is sent with the response. `AuthenticationError`
sets `WWW-Authenticate: Bearer`, which RFC 6750 requires on `401`
responses.

### `build_error_body(error_code, message, details=None) -> dict`

Builds the envelope, filling `request_id` from context. Used by the
handlers here and by `RequestContextMiddleware`.

### `register_exception_handlers(app)`

Registers the three handlers below. Called from `create_app()`.

## How it works

| Handler                   | Catches                  | Response                                    |
|---------------------------|--------------------------|---------------------------------------------|
| `handle_app_error`        | `AppError` subclasses    | Class `status_code` and `error_code`; logged at `INFO` |
| `handle_validation_error` | `RequestValidationError` | `422 validation_error` with `details`       |
| `handle_http_error`       | Starlette `HTTPException` (unknown route, wrong method) | Same status; code derived from the status phrase, e.g. `method_not_allowed` |

`_summarise_validation_errors` keeps only `loc`, `msg`, and `type` from
each Pydantic error. Pydantic also includes the rejected `input`, which
could be a document number or a secret, so it is dropped.

Unexpected exceptions are **not** handled here; see
[middleware](middleware.md#why-crashes-are-handled-here).

## Changing this module

- Prefer subclassing over adding new status mappings.
- Do not raise `fastapi.HTTPException` from services; raise an
  `AppError` subclass instead.

## Tests

`tests/core/test_error_handling.py`.
