# Writing a Connector

> Last updated: 2026-09-28

A connector is the code behind one integration tool (a row in
`integration_tools`). It links one client user to that tool and is the
only code that ever sees a user's decrypted parameters.

There are two ways to give a tool a connector:

| | Configuration (`connector_config`) | Python code |
|--|------------------------------------|-------------|
| Best for | REST/JSON providers such as SurePass | Providers needing custom logic (signing, polling, multi-step flows) |
| Adding one | `PUT /v1/integration-tools/{tool_code}` | A module in `app/connectors/` and a deploy |
| Operations | Declared in the configuration | Override `run_operation` |

A registered Python connector wins over a configuration for the same
tool code.

## Configuration-driven connectors

Save the configuration on the tool with the admin API; no code or
deployment is needed. See [SurePass](integrations/surepass.md) for a
complete walkthrough and `docs/examples/surepass-tool.json` for a
template.

```json
{
  "type": "http",
  "environments": {"sandbox": "https://sandbox.example.com/api/v1"},
  "default_environment": "sandbox",
  "headers": {"Authorization": "Bearer {credentials.api_token}"},
  "timeout_seconds": 30,
  "response": {"success_field": "success", "data_field": "data", "message_field": "message"},
  "operations": {
    "verify_document": {
      "method": "POST",
      "path": "/documents/verify",
      "description": "Check an identity document",
      "query": {},
      "body": {"id_number": "{kwargs.id_number}", "consent": "{kwargs.consent}"}
    }
  },
  "connect_operation": null
}
```

### OAuth 2.0 and Test connection

For service accounts, declare `auth` instead of an `Authorization`
header; the connector obtains and caches the token:

```json
"auth": {
  "type": "oauth2_client_credentials",
  "token_url": "https://login.example.com/{credentials.tenant_id}/oauth2/token",
  "client_id": "{credentials.client_id}",
  "client_secret": "{credentials.client_secret}",
  "scope": "https://api.example.com/.default",
  "client_auth": "body"
}
```

- `token_url` must be HTTPS with a literal host; placeholders are allowed
  in the path only (credentials or settings).
- `client_secret` must be exactly one `{credentials.<name>}` placeholder.
- `client_auth: "basic"` sends the id and secret with HTTP Basic instead
  of in the form body.

`test_operation` names an operation that **Test connection** runs for the
client without any user (so it may not use `args`, `kwargs`, or
`{user_uuid}`). See [client integrations](features/client_integrations.md).

### Placeholders

| Placeholder | Value |
|-------------|-------|
| `{kwargs.<name>}` | Keyword input: saved for the user, overridden by the request |
| `{args.<index>}` | Positional input, from 0 |
| `{credentials.<name>}` | The client's stored credential for the tool |
| `{settings.<name>}` | The client's non-secret setting for the type |
| `{session.<name>}` | A value an earlier flow step captured (flows only) |
| `{user_uuid}`, `{client_id}` | Identifiers of the call |

A value that is exactly one placeholder keeps its JSON type; inside a
longer string it becomes text. Nothing is evaluated as code. Missing
values are reported together as `422 missing_inputs` (or `409
tool_credentials_missing` for credentials).

### Rules enforced when saving

- Environments must be `https` (plain `http` only for `localhost`) and
  plain base URLs. The client picks one by name through its
  `environment` setting and can never supply a URL.
- Operation paths are relative (`/...`); path values are URL-encoded.
- Credentials may not appear in paths (they would reach access logs).
- Headers named like `Authorization`, `*api-key*`, `*token*`, `*secret*`
  must use `{credentials.*}`; a pasted literal token is rejected.
- Headers may not use user inputs.
- Operation names are `lower_snake_case`; they appear in the API path.

### Responses

With the `response` mapping, a JSON reply becomes `success`, `data`,
and `message`. `success` is true only for a 2xx status **and** a truthy
`success_field` (when present). Non-JSON replies, timeouts, and network
errors become `502 operation_failed`.

### Flows

A flow chains operations into a verification (Confirm) or data
gathering (Gather) procedure, run as a [session](features/sessions.md).
Each step names an operation, the `inputs` the holder must have given
before it runs, and values to `capture` from its response for later
steps:

```json
"flows": {
  "aadhaar_otp": {
    "purpose": "verification",
    "description": "Aadhaar number, then the OTP sent to it",
    "steps": [
      {
        "operation": "aadhaar_generate_otp",
        "inputs": ["id_number"],
        "capture": {"request_ref": "client_id"}
      },
      {"operation": "aadhaar_submit_otp", "inputs": ["otp"]}
    ],
    "outputs": {
      "full_name": "full_name",
      "date_of_birth": "dob",
      "provider_reference": "session.request_ref"
    },
    "verified_when": [{"path": "status", "equals": "valid"}]
  }
}
```

Here `aadhaar_submit_otp` would send
`{"client_id": "{session.request_ref}", "otp": "{kwargs.otp}"}`.
The session pauses before step 2 until the OTP is submitted.

- `outputs`: attribute name -> dot path into the **last** step's `data`
  (`a.b`, `items.0.id`), or `session.<name>`. Required for `gather`.
- `verified_when`: conditions on the last step's data that must all
  hold (`equals`, `one_of`, or neither for "present and truthy"). For
  `verification` only; without any, provider success is enough.
- A step whose provider reply is `success: false` ends a verification as
  `not_verified`, and fails a gather session.

Checked when the tool is saved: every operation exists; every
`{kwargs.x}` is declared as an input by that step or an earlier one;
every `{session.x}` and `session.` output is captured by an earlier
step; flows do not use `{args.N}`; headers never use `{session.*}`.

### Built-in tools

A Python connector can set `is_built_in = True` for a tool EveryCRED
provides itself (see `app/connectors/holder_wallet_app.py`). The
Integrations screen then lists it for every client as connected, with
no credentials to store; see
[client integrations](features/client_integrations.md#built-in-tools).

## Python connectors

### 1. Create the module

`app/connectors/<tool_code>.py`:

```python
"""Connector for the Acme Verify identity tool."""

from app.connectors.base import (
    ConnectionOutcome,
    ConnectorError,
    IntegrationConnector,
)
from app.connectors.registry import register_connector


@register_connector("acme-verify")
class AcmeVerifyConnector(IntegrationConnector):
    """Links a user to their Acme Verify account."""

    async def connect(
        self, api_token: str, *, region: str = "in"
    ) -> ConnectionOutcome:
        """Verify the token with Acme and return the account id."""
        response = await self.context.http_client.post(
            "https://api.example.com/v1/accounts/verify",
            headers={"Authorization": f"Bearer {api_token}"},
            json={"region": region},
        )
        if response.status_code == 401:
            raise ConnectorError("Acme rejected the API token.")
        response.raise_for_status()
        return ConnectionOutcome(
            details={"account_id": response.json()["account_id"]}
        )
```

The string passed to `@register_connector` must match an
`integration_tools.code`. One connector serves every type its tool is
linked to; `self.context.integration_type_code` says which one a call
is for.

### 2. Register the import

Add the module to `app/connectors/__init__.py` so its decorator runs:

```python
from app.connectors import acme_verify  # noqa: F401
```

### 3. Rules

- **Signature is the contract.** Parameters the client saves are bound
  against `connect`. Use explicit names, defaults for optional values,
  and keyword-only (`*`) for anything that should not be positional.
- **Operations are optional.** Override `run_operation(self, operation,
  *args, **kwargs)` and `describe_operations` to offer them; raise
  `UnknownOperationError` for names you do not support.
- **Use `self.context`.** It holds `credentials` (read from the secret
  store for this call only), `client_id`, `user_uuid`,
  `client_settings` (the client's non-secret settings for this type),
  and the shared `http_client`. Never create your own HTTP client.
- **Raise `ConnectorError` for expected failures.** Its message is
  stored and shown to the client, so describe the problem without
  tokens, raw responses, or personal data.
- **Let unexpected errors propagate.** The service records them as
  "The integration failed unexpectedly." and logs only the exception
  type and stack.
- **Return only non-secret details.** `ConnectionOutcome.details` is
  stored as plain JSON and returned to the client.
- **Stay within the timeout** (`CONNECTOR_TIMEOUT_SECONDS`). Long
  operations belong in a background job.
- **Do not log parameters.**

### 4. Test it

Mock the provider with `httpx.MockTransport` and exercise the connector
directly, then add an API test that overrides `get_connector_registry`
(see `tests/features/user_connections/conftest.py`).
