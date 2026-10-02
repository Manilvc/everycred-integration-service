# Integration Tools

> Package: `app/features/integration_tools/`
> Last updated: 2026-10-02

## Overview

An **integration tool** is an external product that fulfils one or more
integration types: for example, one KYC vendor's API might serve both
Confirm and Gather. Tools are stored in the database, so a new one
appears in the listings as soon as its row exists.

Each client configuration names the tool the client uses for a type
(chosen by a super admin). When a client's user connects, the service
runs that **tool's connector** (see [writing a connector](../connectors.md)).

```mermaid
erDiagram
    integration_types ||--o{ integration_tool_types : "served by"
    integration_tools ||--o{ integration_tool_types : "serves"
    integration_tools ||--o{ client_integration_configs : "chosen in"
    integration_types ||--o{ client_integration_configs : "configured as"
```

## Endpoints

| Method | Path | Auth | Summary |
|--------|------|------|---------|
| GET | `/v1/integration-tools` | Super admin | Whole catalogue |
| GET | `/v1/client/integration-tools` | `X-API-Key` | Tools usable for the calling client |
| PUT | `/v1/integration-tools/{tool_code}` | Super admin | Create or replace a tool, including its `connector_config` |
| GET | `/v1/integration-tools/{tool_code}/fields` | Super admin | The tool's global field keys (set with `fields` on `PUT /v1/integration-tools/{tool_code}`) |
| GET, PUT | `/v1/clients/{client_id}/tools/{tool_code}/fields` | Super admin | One client's own field keys |
| GET | `/v1/client/integration-tools/{tool_code}/fields` | `X-API-Key` | Global and own field keys, for a tool serving one of the client's enabled types |

### Admin listing

Query parameters: `limit`, `offset`, `integration_type` (type code),
`include_inactive` (default `false`).

### Client listing

Query parameters: `limit`, `offset`, `integration_type`. Returns only
**active** tools that serve at least one type **enabled for the
client**, and each tool lists only those enabled types, so a client
never learns about other types.

### Response item

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "code": "acme-verify",
  "name": "Acme Verify",
  "provider": "Acme",
  "description": null,
  "is_active": true,
  "display_order": 10,
  "integration_types": [
    {"code": "confirm", "name": "Confirm"},
    {"code": "gather", "name": "Gather"}
  ],
  "connector": {
    "is_available": true,
    "parameters": [
      {"name": "api_token", "kind": "positional_or_keyword", "required": true, "annotation": "str"},
      {"name": "region", "kind": "keyword_only", "required": false, "annotation": "str"}
    ]
  },
  "created_at": "2026-09-28T12:00:00Z",
  "updated_at": "2026-09-28T12:00:00Z"
}
```

`connector.parameters` is read from the connector's `connect`
signature at request time, so it always matches the code. Clients use
it to know which `args` and `kwargs` to save for their users. Default
values are never published.

## Adding a tool

Use the admin API; it validates everything, including the connector
configuration (see [connectors](../connectors.md)):

```bash
curl -X PUT http://localhost:8000/v1/integration-tools/surepass \
  -H "Authorization: Bearer <super admin token>" \
  -H "Content-Type: application/json" \
  --data @docs/examples/surepass-tool.json
```

The whole definition is replaced on each call. The listing's
`connector.kind` is `http` for configured tools and `code` for Python
connectors, and `connector.operations` lists each operation with the
`kwargs` and credentials it needs.

Tools can also be inserted with SQL, then linked to the types they
serve:

```sql
INSERT INTO integration_tools
    (id, code, name, provider, description, is_active, display_order, created_at, updated_at)
VALUES
    (REPLACE(UUID(), '-', ''), 'acme-verify', 'Acme Verify', 'Acme', NULL, 1, 10,
     UTC_TIMESTAMP(), UTC_TIMESTAMP());

INSERT INTO integration_tool_types (integration_tool_id, integration_type_id)
SELECT tool.id, type.id
FROM integration_tools AS tool
JOIN integration_types AS type ON type.code IN ('confirm', 'gather')
WHERE tool.code = 'acme-verify';
```

Then add its connector in `app/connectors/acme_verify.py` with
`@register_connector("acme-verify")`. Until then the tool is listed
with `connector.is_available: false` and connect calls answer `501`.

- Hide a tool with `is_active = 0`; it disappears from client listings
  and can no longer be chosen, and connects through it answer `409`.
- A tool still chosen by any client cannot be deleted (`RESTRICT`).
- Treat `code` as permanent: it links the row to its connector.

## Choosing a tool for a client

Super admins pass `tool_code` when setting a client's configuration:

```bash
curl -X PUT http://localhost:8000/v1/clients/<client_id>/integrations/confirm \
  -H "Authorization: Bearer <super admin token>" \
  -H "Content-Type: application/json" \
  -d '{"is_enabled": true, "tool_code": "acme-verify", "settings": {}}'
```

| Status | `error.code` | When |
|--------|--------------|------|
| 404 | `integration_tool_not_found` | No tool has that code |
| 422 | `integration_tool_not_usable` | Tool does not serve the type, or is inactive |

Sending `"tool_code": null` (or omitting it) clears the choice. The
`PUT` replaces the whole configuration, as before.

## Field keys

A reference list, entered by a super admin, of the keys a flow's
provider response holds, such as `full_name`, `dob`, or `address.zip`
(nested fields use dots). Only keys are stored, never values. Each field
has `flow`, `key`, an optional `label`, and a `value_type` (`string`,
the default, or `number`, `boolean`, `object`, `array`).

**Global fields** apply to every client and are sent with the tool:

```json
PUT /v1/integration-tools/surepass
{
  "name": "SurePass",
  "integration_types": ["confirm"],
  "connector_config": {"...": "..."},
  "fields": [
    {"flow": "aadhaar_otp", "key": "full_name", "label": "Full name"},
    {"flow": "aadhaar_otp", "key": "dob", "label": "Date of birth"},
    {"flow": "aadhaar_otp", "key": "address.zip", "label": "PIN code"}
  ]
}
```

`fields` replaces the global list; leave it out to keep the list, send
`[]` to remove it.

**Client fields** add to the global ones for one client:

```json
PUT /v1/clients/{client_id}/tools/surepass/fields
{"fields": [{"flow": "aadhaar_otp", "key": "care_of", "label": "Care of"}]}
```

The body replaces that client's list. `GET` on the same path lists them.

**Reading them** from a client backend returns both, each with its
`scope`:

```json
GET /v1/client/integration-tools/surepass/fields?flow=aadhaar_otp
{
  "items": [
    {"id": "3f6b…", "flow": "aadhaar_otp", "key": "care_of", "label": "Care of",
     "value_type": "string", "scope": "client", "created_at": "…", "updated_at": "…"},
    {"id": "a91c…", "flow": "aadhaar_otp", "key": "full_name", "label": "Full name",
     "value_type": "string", "scope": "global", "created_at": "…", "updated_at": "…"}
  ],
  "total": 2, "limit": 20, "offset": 0
}
```

Each field has a stable `id`: saving a list again updates fields
that stay in it (same flow and key) in place, so their ids never
change and can be stored as references. Only new fields get new ids,
and removed fields are deleted.

Rules: keys are up to 8 segments of letters, digits, `_` or `-` joined
by dots; a flow and key may appear once per list (`422` otherwise); at
most 300 fields per list; when the tool has flows configured, every
field must name one of them (`422 unknown_tool_flows`). Lists are
ordered by flow, then key, with global fields before client ones for
the same key.

| Column | Meaning |
|--------|---------|
| `integration_tool_id`, `client_id`, `flow`, `key` | Unique together; `client_id` empty for global fields |
| `label` | Optional display name |
| `value_type` | `string`, `number`, `boolean`, `object`, or `array` |

## Data model

| Table | Key columns | Notes |
|-------|-------------|-------|
| `integration_tools` | `code` (unique), `name`, `provider`, `is_active`, `display_order` | |
| `integration_tool_types` | (`integration_tool_id`, `integration_type_id`) primary key | Rows go with either side (`CASCADE`) |
| `client_integration_configs.integration_tool_id` | nullable FK | `RESTRICT` on tool delete |

Migration: `f536a4db8356`.

## Testing

- `tests/features/integration_tools/test_listing.py` — admin and client
  listings, filters, parameter description.
- `tests/features/integration_tools/test_client_configuration.py` —
  tool choice validation and client-edited settings.
