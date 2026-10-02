# Client Integrations (the Integrations screen)

> Package: `app/features/client_integrations/`
> Last updated: 2026-10-02

## Overview

The APIs behind a client admin's **Integrations** screen: tools grouped
by integration type, each card showing its status, and a drawer to
store credentials, switch the tool on or off, and **Test connection**.

They are called by the client's own backend with its `X-API-Key`. The
listing has every active type, but a client only gets the active tools
serving the types a super admin enabled for it, plus **built-in** tools
such as the Holder Wallet App, which every client gets. Which tool its
users are routed through for a type remains a super admin choice.

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
| GET | `/v1/client/integrations` | The page: groups and their systems |
| GET | `/v1/client/integrations/{tool_code}` | The drawer |
| PUT | `/v1/client/integrations/{tool_code}` | **Save**: switch and credentials |
| POST | `/v1/client/integrations/{tool_code}/test` | **Test connection** |

The same listing for one of the client's users, with the user's
connection status, is
[`GET /v1/client/users/{user_uuid}/integrations`](user_connections.md#a-users-integrations-with-connection-status).

### List

Same envelope and field names as the EveryCRED Integrations screen
(`apps/v1/api/integrations` in the EveryCRED backend), so its frontend
renders it unchanged. Ids are this service's UUIDs.

```json
{
  "status": "success",
  "data": {
    "groups": [
      {
        "id": "a62b8f29-af24-4b69-9b6c-e00e17afff08",
        "key": "confirm",
        "label": "CONFIRM - Identity & verification",
        "description": "Identity & verification",
        "systems": [
          {
            "id": "5c1e0c6e-8f0e-4d55-9a57-3f4f1b0b2c11",
            "code": "surepass",
            "source_role_id": "a62b8f29-af24-4b69-9b6c-e00e17afff08",
            "name": "SurePass",
            "status_note": "Indian KYC: Aadhaar OTP and PAN verification.",
            "is_connected": true,
            "is_active": true,
            "is_default": true,
            "status": "connected",
            "last_tested_at": "2026-10-01T08:15:00Z"
          }
        ]
      },
      {
        "id": "…",
        "key": "declare",
        "label": "DECLARE - Holder & issuer input",
        "description": "Holder & issuer input",
        "systems": [
          {
            "id": "…",
            "code": "holder-wallet-app",
            "source_role_id": "…",
            "name": "Holder Wallet App",
            "status_note": "Wallet Application",
            "is_connected": true,
            "is_active": true,
            "is_default": true,
            "status": "connected",
            "last_tested_at": null
          }
        ]
      }
    ]
  },
  "message": "Integrations retrieved successfully."
}
```

| Field | Source |
|-------|--------|
| group `id`, `key`, `description` | The integration type's id, `code`, `description` |
| group `label` | `"<NAME> - <description>"`, or just the name in capitals when there is no description |
| system `id`, `code`, `name`, `status_note` | The tool's id, `code`, `name`, `description` |
| system `source_role_id` | The group's `id` |
| system `is_connected` | `status` is `connected` or `configured` |
| system `is_active` | The client has the tool switched on |
| system `is_default` | Built-in tool, or the tool the type routes users through |
| system `code` | Extra to the EveryCRED shape: needed for the drawer, Save, and Test connection |

Groups follow the types' `display_order`. A type the client has not
enabled is listed with built-in systems only (often none), so the
screen always shows the same groups.

### Built-in tools

A tool whose connector sets `is_built_in = True` is provided by
EveryCRED itself; `holder-wallet-app` (`app/connectors/holder_wallet_app.py`,
seeded by migration `c5f3f5bf92e7`) is the first. It needs no
credentials, is listed for every client, and shows `connected` unless
the client switches it off with Save (`{"is_enabled": false}`). Test
connection succeeds without calling anything.

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
  Per-client field mappings existed briefly and were removed (migration
  `82996fd0ddf2`); the keys a provider returns are recorded instead
  (see [sessions](sessions.md#recorded-field-keys)).
- **Deployment** (on-premises vs cloud) is not modelled.
- The type codes stay `enforcement` and `records`; EveryCRED's own
  screen uses `enforce` and `record` for those groups.

## Testing

- `tests/features/client_integrations/test_integrations_api.py` —
  the EveryCRED response shape, grouping, statuses, built-in tools,
  scoping, credential merge, tests, and disabling.
- `tests/connectors/test_oauth.py` — token requests, caching, retry,
  client auth styles, and configuration rules.
