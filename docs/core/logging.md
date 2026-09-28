# `app/core/logging.py` and `app/core/context.py`

> Last updated: 2026-09-28

## Purpose

Give every log line the same format and tie it to the request that
produced it. `context.py` holds the request-scoped values;
`logging.py` reads them when formatting records.

## Public API

### `context.request_id_context`

A `ContextVar[str | None]` set by `RequestContextMiddleware` for the
duration of each request. Code never sets it directly.

### `context.get_request_id() -> str | None`

Returns the current request id, or `None` outside a request (startup,
background scripts).

### `context.REQUEST_ID_HEADER`

`"X-Request-ID"` — shared by the middleware (incoming and response
header) and the HTTP client (outbound header).

### `logging.configure_logging(level, use_json)`

Called once from `create_app()`. Replaces the root logger's handlers
with a single stdout handler.

Modules log through the standard library:

```python
import logging

logger = logging.getLogger(__name__)
logger.info("Verification %s submitted to %s", verification_id, provider)
```

## How it works

1. **Context variables** — `ContextVar` values are copied into each
   asyncio task, so concurrent requests never see each other's id, and
   tasks started from a request inherit it.
2. **`RequestIdFilter`** — attached to the handler, it copies
   `get_request_id()` onto every record as `record.request_id` (or `-`
   when there is none). Doing this in a filter means third-party
   loggers get the id too.
3. **Formatters** — `JsonFormatter` emits `timestamp` (UTC ISO 8601),
   `level`, `logger`, `message`, `request_id`, and `exception` when a
   traceback is attached. The text format carries the same fields for
   local reading.
4. **Uvicorn** — `uvicorn` and `uvicorn.error` lose their own handlers
   and propagate to the root, so startup messages share the format.
   `uvicorn.access` is disabled because the middleware writes a richer
   access line (status, duration, request id).

## Configuration

| Env var     | Default | Effect                        |
|-------------|---------|-------------------------------|
| `LOG_LEVEL` | `INFO`  | Root logger level             |
| `LOG_JSON`  | `true`  | `false` switches to text lines |

## Rules for callers

- Use `%s` arguments, not f-strings, so formatting is skipped for
  suppressed levels.
- Never log secrets, tokens, provider credentials, document images, or
  personal data (names, emails, ID numbers). Log internal ids instead.
- Use `logger.exception(...)` inside `except` blocks to keep the
  traceback.

## Changing this module

- To add another context field (for example `tenant_id`), add a
  `ContextVar` and getter in `context.py`, set it in middleware or a
  dependency, and add it in both `RequestIdFilter` and `JsonFormatter`.
- `configure_logging` clears existing root handlers; calling it twice
  is safe but will drop handlers other code added.
