# Webhooks

> Package: `app/features/webhooks/`
> Last updated: 2026-09-29

## Overview

A client registers one HTTPS endpoint to be told when its
[sessions](sessions.md) finish, instead of polling. Events carry the
session's status and ids only, never inputs or attributes; fetch those
from the result endpoint.

## Endpoints

All routes require `X-API-Key`.

| Method | Path | Summary |
|--------|------|---------|
| GET    | `/v1/client/webhook` | Show the endpoint (no secret) |
| PUT    | `/v1/client/webhook` | Create or change it (`url`, `is_active`, `rotate_secret`) |
| DELETE | `/v1/client/webhook` | Remove it and its secret |
| POST   | `/v1/client/webhook/test` | Send a `webhook.test` event now |

The response to the first `PUT`, and to one with `rotate_secret: true`,
includes `signing_secret` (`whsec_...`). It is shown only then; store it
in your backend's secrets.

URLs must be `https`, without credentials, and resolve to public
addresses. Private, loopback and link-local destinations are refused
(also at delivery time, in case DNS changes), unless
`WEBHOOK_ALLOW_PRIVATE_NETWORKS=true`, which is meant for local
development only.

## Events

| Event | When |
|-------|------|
| `session.completed` | The provider answered; see `data.outcome`. |
| `session.failed` | No answer could be obtained; see `data.failure_code`. |
| `session.expired` | Not finished in time. |
| `webhook.test` | Sent by the test endpoint. |

```json
{
  "id": "5f0c...",
  "event": "session.completed",
  "created_at": "2026-09-29T10:05:00+00:00",
  "data": {
    "session_id": "0b8e...",
    "user_uuid": "4444...",
    "reference": "invite-7",
    "integration_type": "confirm",
    "tool_code": "surepass",
    "flow": "aadhaar_otp",
    "purpose": "verification",
    "status": "completed",
    "outcome": "verified",
    "failure_code": null,
    "result_attributes": ["date_of_birth", "full_name"]
  }
}
```

Headers:

| Header | Value |
|--------|-------|
| `X-EveryCRED-Event` | Event name |
| `X-EveryCRED-Delivery` | Delivery id; the same on retries, use it to de-duplicate |
| `X-EveryCRED-Signature` | `t=<unix seconds>,v1=<hex HMAC-SHA256>` |

## Verifying the signature

The signature is `HMAC-SHA256(secret, "<t>." + raw_body)`. Verify the
raw bytes before parsing JSON, and reject old timestamps:

```python
import hashlib
import hmac
import time


def is_valid(secret: str, header: str, body: bytes) -> bool:
    """Check X-EveryCRED-Signature; tolerate 5 minutes of clock skew."""
    parts = dict(item.split("=", 1) for item in header.split(","))
    timestamp = int(parts["t"])
    if abs(time.time() - timestamp) > 300:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

The same logic is in `app.features.webhooks.delivery.verify_signature`.

## Delivery and retries

Events are queued in `webhook_deliveries` in the same transaction as
the session change, and sent by the [worker](sessions.md#worker).

- A `2xx` answer means delivered. Answer quickly and do the work
  asynchronously; the timeout is `WEBHOOK_TIMEOUT_SECONDS` (10).
- `5xx`, `408`, `429`, timeouts and network errors are retried after
  30 s, 2 min, 10 min, 30 min, then hourly, up to
  `WEBHOOK_MAX_ATTEMPTS` (8).
- Other `4xx` answers are not retried.
- Redirects are not followed.

Delivery is at-least-once: de-duplicate on `X-EveryCRED-Delivery`.
