# `app/core/http_client.py`

> Last updated: 2026-09-28

## Purpose

Provide one pooled `httpx.AsyncClient` for all outbound calls to KYC
providers and other external APIs. Creating a client per call throws
away connection reuse and TLS session caching, which matters when a
provider is called on every verification.

## Public API

### `HttpClient`

`Annotated[httpx.AsyncClient, Depends(get_http_client)]`. Inject it
into provider adapters through a dependency:

```python
def get_provider_client(http_client: HttpClient) -> ExampleKycClient:
    return ExampleKycClient(http_client, base_url=...)
```

### `create_http_client(settings) -> httpx.AsyncClient`

Called once by the lifespan in `app.main`.

## How it works

1. **Timeouts** — every request gets `HTTP_TIMEOUT_SECONDS` for connect,
   read, write, and pool acquisition. Override per call when a provider
   needs longer: `await client.post(url, timeout=30.0)`.
2. **Pool limits** — `HTTP_MAX_CONNECTIONS` caps concurrent sockets so a
   slow provider cannot exhaust file descriptors.
3. **Redirects** — disabled. A redirected API call could forward an
   `Authorization` header to a host the provider did not intend.
4. **Tracing** — the `_forward_request_id` event hook adds
   `X-Request-ID` to every outbound request, so a provider's support
   team can find our call in their logs.
5. **User-Agent** — `<service_name>/<service_version>`.
6. **Shutdown** — the lifespan calls `aclose()`.

## Failure modes

httpx raises `httpx.TimeoutException`, `httpx.ConnectError`, or
`httpx.HTTPStatusError` (after `response.raise_for_status()`). Provider
adapters should catch these, log them, and raise
`ExternalServiceError` with a message that is safe to show clients.

## Changing this module

- Put provider-specific headers and base URLs in the adapter, not in
  the shared client.
- Retries are not built in. Add them per adapter, only for idempotent
  requests, with a bounded count and backoff.
