"""OpenAPI metadata rendered by ReDoc (``/redoc``) and Swagger (``/docs``).

Keeping the prose here keeps :mod:`app.main` about wiring. Tag names
must match the ``tags`` declared on each feature's router; a tag used
by a router but missing here still works, it just has no description.
"""

from typing import Any

from fastapi import FastAPI

API_DESCRIPTION = """
Central service for EveryCRED's external identity and verification
integrations. Client projects are registered by platform super admins,
receive API keys, and use them to read their configuration and connect
their users to integration tools.

## Authentication

The API has two kinds of callers, each with its own credential.

- **Super admins** send the access token from
  `POST /v1/super-admins/login` as `Authorization: Bearer <token>`.
- **Client projects** send the API key a super admin issued them as
  `X-API-Key: <key>`.

Access tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (30 by
default). API keys last until they expire or are revoked. Each endpoint
lists the credential it accepts under **Authorizations**.

## Errors

Every error response has the same shape:

```json
{
  "error": {
    "code": "client_not_found",
    "message": "Client 00000000-0000-0000-0000-000000000000 does not exist.",
    "request_id": "6f37b4e671f344f99e4a0736491737ea"
  }
}
```

- `code` is stable and safe to branch on; `message` is for people.
- Validation errors (`422`) add a `details` list with the location and
  reason of each problem. Submitted values are never echoed back.
- `request_id` matches the `X-Request-ID` response header. Quote it when
  reporting a problem.

## Request ids

Send `X-Request-ID` (letters, digits, `.`, `_`, `-`; up to 128
characters) to correlate calls with your own logs. Otherwise the
service generates one. Either way it is returned on the response.

## Pagination

List endpoints accept `limit` (1–100, default 20) and `offset`
(default 0) and return `items`, `total`, `limit`, and `offset`.
"""

OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "Health",
        "description": (
            "Liveness and readiness probes for load balancers and "
            "orchestrators. No authentication."
        ),
    },
    {
        "name": "Super Admins",
        "description": (
            "Platform operators. The first super admin is registered "
            "with the one-time `X-Bootstrap-Token`; every later account "
            "must be registered by a signed-in super admin. Repeated wrong "
            "passwords lock an account (5 attempts, 15 minutes by "
            "default), and every login "
            "failure returns the same `invalid_credentials` error."
        ),
    },
    {
        "name": "Integration Types",
        "description": (
            "Catalogue of integration types such as Confirm, Gather, "
            "Enforcement, and Records. Types are stored in the database, "
            "so new ones appear here without a deployment. Super admin "
            "only."
        ),
    },
    {
        "name": "Integration Tools",
        "description": (
            "External tools that fulfil integration types. One tool can "
            "serve several types. A tool is served either by Python "
            "code or, for REST providers such as SurePass, by a "
            "`connector_config` saved with **Create or replace an "
            "integration tool**: base URLs, auth headers, and operations "
            "as data, with no deployment. Each tool reports its "
            "connector kind and the operations and inputs it accepts. "
            "Super admin only."
        ),
    },
    {
        "name": "Clients",
        "description": (
            "Client projects that integrate with the service: register "
            "them, issue and revoke their API keys, and choose which "
            "integration types each one uses and with which tool. "
            "Super admin only.\n\n"
            "A new API key is returned **once**, in the response that "
            "creates it. Only a hash is stored, so a lost key cannot be "
            "recovered; issue a new one and revoke the old one."
        ),
    },
    {
        "name": "Client Self-Service",
        "description": (
            "Called by client projects with their `X-API-Key`. Read "
            "your configuration, list the tools available to you, and "
            "update your own settings for integrations a super admin "
            "has enabled. Enabling integrations and choosing tools stay "
            "with super admins."
        ),
    },
    {
        "name": "Client Integrations",
        "description": (
            "The client admin's Integrations screen. Lists the types a "
            "super admin enabled for the client, each with its tool "
            "cards and status, and lets the client store credentials, "
            "switch a tool on or off, and run **Test connection**. "
            "OAuth 2.0 service accounts are supported: the service "
            "obtains and caches the token. Called with the client's "
            "`X-API-Key`; credential values are never returned."
        ),
    },
    {
        "name": "User Connections",
        "description": (
            "Connect a client's users to its enabled integrations. Save "
            "the `args` and `kwargs` the integration's connector needs, "
            "then call **connect**, which runs "
            "`connector.connect(*args, **kwargs)` for the tool the "
            "client uses for that type. Run a tool's **operations** "
            "(for example a document check) for a user with the "
            "operations endpoint; its result is normalised to "
            "`success`, `data`, and `message`.\n\n"
            "Inputs and the client's tool credentials live in the "
            "secret store (AWS Secrets Manager encrypted with KMS); the "
            "database keeps references only, and values are never "
            "returned. Called with the client's `X-API-Key`; the client "
            "is taken from the key, so it can only reach its own users."
        ),
    },
    {
        "name": "Sessions",
        "description": (
            "Verify a user's identity (**Confirm**) or fetch their data "
            "from a system of record (**Gather**) by running one of the "
            "tool's configured flows. Start a session; it runs until a "
            "step needs holder input, such as an OTP, and reports "
            "`awaiting_input` with the names it needs. Submit them to "
            "the inputs endpoint. Learn the outcome from the "
            "`session.completed` / `session.failed` / `session.expired` "
            "webhook or by polling, then read the attributes from the "
            "result endpoint.\n\n"
            "Inputs are held in the secret store only while the session "
            "is active; results are stored encrypted and deleted "
            "automatically at `data_expires_at`. Status responses and "
            "webhooks never contain personal data."
        ),
    },
    {
        "name": "Webhooks",
        "description": (
            "Register the HTTPS endpoint that receives session events. "
            "Each delivery is signed: `X-EveryCRED-Signature` is "
            '`t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">` with '
            "the signing secret shown once when the endpoint is created "
            "or the secret rotated. Failed deliveries are retried with "
            "backoff."
        ),
    },
]

# ReDoc extension: groups tags in the sidebar by who calls them.
TAG_GROUPS: list[dict[str, Any]] = [
    {
        "name": "Platform administration",
        "tags": [
            "Super Admins",
            "Integration Types",
            "Integration Tools",
            "Clients",
        ],
    },
    {
        "name": "Client API",
        "tags": [
            "Client Self-Service",
            "Client Integrations",
            "User Connections",
            "Sessions",
            "Webhooks",
        ],
    },
    {"name": "Operations", "tags": ["Health"]},
]

LICENSE_INFO = {"name": "MIT", "identifier": "MIT"}


def add_redoc_extensions(app: FastAPI) -> None:
    """Add ReDoc-only extensions to the generated OpenAPI schema.

    FastAPI builds the schema lazily on the first request to
    ``/openapi.json`` and caches it; this wraps that step once so the
    extensions are included in the cached copy.
    """
    build_schema = app.openapi

    def openapi_with_tag_groups() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = build_schema()
            schema["x-tagGroups"] = TAG_GROUPS
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi_with_tag_groups  # type: ignore[method-assign]
