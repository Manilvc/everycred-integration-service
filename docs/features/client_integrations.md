# Client Integrations (the Integrations screen)

> Package: `app/features/client_integrations/`
> Last updated: 2026-09-29

## Overview

The APIs behind a client admin's **Integrations** screen: tools grouped
by integration type, each card showing its status, and a drawer to
store credentials, switch the tool on or off, and **Test connection**.

They are called by the client's own backend with its `X-API-Key`. A
client sees only the types a super admin enabled for it and the active
tools serving them. Which tool its users are routed through for a type
(`is_default`) remains a super admin choice.

```mermaid
flowchart LR
    UI[Client admin UI] --> BE[Client backend]
    BE -- X-API-Key --> L[GET /client/integrations]
    BE -- X-API-Key --> D[GET/PUT /client/integrations/{tool}]
    BE -- X-API-Key --> T[POST /client/integrations/{tool}/test]
    D --> SM[(Secrets Manager + KMS)]
    T --> P[Provider]
```

## Endpoints

| Method | Path | Screen element |
|--------|------|----------------|
| GET | `/api/v1/client/integrations` | The page: groups and tool cards |
| GET | `/api/v1/client/integrations/{tool_code}` | The drawer |
| PUT | `/api/v1/client/integrations/{tool_code}` | **Save**: switch and credentials |
| POST | `/api/v1/client/integrations/{tool_code}/test` | **Test connection** |

### List

```json
{
  "groups": [
    {
      "integration_type": {"code": "confirm", "name": "Confirm", "description": "Is this really the person?"},
      "tools": [
        {"code": "entra", "name": "Microsoft Entra ID", "provider": "Microsoft",
         "status": "connected", "is_enabled": true, "is_default": true,
         "last_tested_at": "2026-09-29T08:15:00Z"}
      ]
    }
  ]
}
```

Groups follow the types' `display_order`; the heading question comes
from the type's `description`.

### Card status

| `status` | Meaning | Suggested label |
|----------|---------|-----------------|
| `available` | Nothing set up yet | Available |
| `needs_credentials` | Some required credentials are missing | Needs setup |
| `not_tested` | Credentials stored, not tested since last change | Not tested |
| `configured` | Credentials stored; the tool has no test call | Enabled |
| `connected` | Last test reached the provider and succeeded | Connected |
| `failed` | Last test failed; see `last_test_message` | Failed |
| `disabled` | The client switched it off | Disabled |
| `unavailable` | No connector installed for the tool yet | Coming soon |

### Drawer

```json
{
  "code": "entra",
  "name": "Microsoft Entra ID",
  "integration_types": [{"code": "confirm", "name": "Confirm", "description": "..."}],
  "connector_kind": "http",
  "auth_method": "oauth2_client_credentials",
  "required_credentials": ["client_id", "client_secret", "tenant_id"],
  "stored_credentials": ["client_id", "tenant_id"],
  "missing_credentials": ["client_secret"],
  "can_test": true,
  "operations": ["organisation"],
  "is_enabled": true,
  "status": "needs_credentials",
  "last_tested_at": null,
  "last_test_message": null
}
```

Build the credential form from `required_credentials`; show a "stored"
marker for names in `stored_credentials`. Values are never returned.

### Save

```json
{"is_enabled": true, "credentials": {"client_secret": "<value>"}}
```

- `credentials` are **merged** into what is stored: send only fields
  being set or rotated. `remove_credentials: ["name"]` deletes one;
  removing all deletes the stored secret.
- Changing credentials resets the status to `not_tested`.
- `is_enabled: false` blocks the client's user connections and
  operations through this tool (`409 integration_tool_disabled`).

### Test connection

```json
{"success": true, "called_provider": true, "status": "connected",
 "message": "Test call succeeded (prod).", "tested_at": "2026-09-29T08:15:00Z"}
```

The test runs, in order of preference:

1. the tool's `test_operation` (a provider call that needs no user);
2. an OAuth token request with the stored credentials, bypassing the
   cache;
3. otherwise, only a check that every required credential is stored
   (`called_provider: false`, status `configured`).

A failed test is `200` with `success: false` and the reason, so the
drawer can display it.

## OAuth 2.0 service accounts

Tools such as Entra ID use the client credentials grant. The tool's
`connector_config` declares it (see [connectors](../connectors.md)):

```json
"auth": {
  "type": "oauth2_client_credentials",
  "token_url": "https://login.microsoftonline.com/{credentials.tenant_id}/oauth2/v2.0/token",
  "scope": "https://graph.microsoft.com/.default"
}
```

The client stores `tenant_id`, `client_id`, and `client_secret` through
**Save**. The service requests tokens, caches them until a minute before
expiry, and retries once with a new token if the provider answers `401`.

## Data model

Table `client_tool_connections` (migration `e48d40f5d0c3`): one row per
client and tool with `is_enabled`, `status`, `last_tested_at`, and
`last_test_message`. Credentials stay in `client_tool_credentials` as a
secret-store reference.

Without a row, a tool counts as enabled once credentials are stored, so
setups made through the super admin API keep working.

## Not included

- **Field mappings** and **sync schedules** from the prototype drawer.
- **Deployment** (on-premises vs cloud) is not modelled.
- Types stay `confirm`, `gather`, `enforcement`, `records`; the
  prototype's *Declare* group has no type yet.

## Testing

- `tests/features/client_integrations/test_integrations_api.py` —
  grouping, statuses, scoping, credential merge, tests, and disabling.
- `tests/connectors/test_oauth.py` — token requests, caching, retry,
  client auth styles, and configuration rules.
