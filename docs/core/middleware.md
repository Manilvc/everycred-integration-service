# `app/core/middleware.py`

> Last updated: 2026-09-28

## Purpose

`RequestContextMiddleware` wraps every HTTP request. It assigns the
request id, writes the access log line, and is the last line of defence
against unhandled exceptions.

## Public API

### `RequestContextMiddleware(app)`

Pure ASGI middleware. Registered in `create_app()` as the **last**
`add_middleware` call, which makes it the outermost layer.

## How it works

1. **Non-HTTP scopes** (`lifespan`, `websocket`) pass straight through.
2. **Request id** — `_resolve_request_id` accepts an incoming
   `X-Request-ID` only if it matches `^[A-Za-z0-9._-]{1,128}$`;
   otherwise it generates `uuid4().hex`. The id is written to logs, so
   unvalidated values would allow log injection.
3. **Context** — the id is stored in `request_id_context`; the token is
   reset in `finally` so it never leaks to the next request on the same
   task.
4. **Response header** — `send_with_request_id` wraps ASGI `send`. On
   `http.response.start` it records the status and appends
   `X-Request-ID` to the outgoing headers.
5. **Unhandled exceptions** — anything not converted to a response by
   the handlers in `exceptions.py` reaches this `except Exception`. It is
   logged with the traceback. If headers have not been sent yet, a
   generic `500 internal_error` envelope is returned; otherwise the
   exception is re-raised because the response can no longer be
   changed.
6. **Access log** — `finally` logs `METHOD path -> status in N ms`.

### Why plain ASGI

Starlette's `BaseHTTPMiddleware` runs the downstream app in a separate
task and buffers streaming responses. Plain ASGI avoids both, keeps
context variables intact, and adds almost no overhead.

### Why crashes are handled here

A handler registered for `Exception` in FastAPI runs in Starlette's
`ServerErrorMiddleware`, which sits *outside* user middleware. By then
this middleware has reset the request id, so the crash log and the
error body would have no id. Catching here keeps them correlated.

## Failure modes

| Situation                        | Behaviour                                    | Logged as           |
|----------------------------------|----------------------------------------------|---------------------|
| Exception before response starts | `500` with `internal_error` envelope         | `ERROR` + traceback |
| Exception during streaming       | Re-raised; server closes connection          | `ERROR` + traceback |
| Invalid incoming request id      | Replaced with a generated id                 | —                   |

## Changing this module

- Keep it outermost. Middleware added after it would run outside the
  request context.
- New per-request context (tenant, user) that comes from headers can be
  set here; context derived from authentication belongs in a dependency.

## Tests

`tests/core/test_middleware.py` and the crash case in
`tests/core/test_error_handling.py`.
