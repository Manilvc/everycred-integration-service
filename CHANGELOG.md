# Changelog

## Unreleased

### Breaking

- User connections are per tool. `user_integration_connections` is
  unique per (client, user, type, tool) instead of per type (migration
  `4c7d2e9b1a58` assigns existing connections their routed tool), and
  `DELETE /v1/client/users/{user_uuid}/connections/{type}` removes only
  one tool's connection.
- Settings sent to `PUT /v1/clients/{client_id}/integrations/{type}`
  with a `tool_code` now belong to that tool only, not to the type, so
  each tool keeps its own `environment`. `PUT /v1/client/integrations/
  {type}/settings` writes to the type's default tool. Migration
  `8e4a1f6c2d93` moves existing settings onto the routed tool.
- `integration_tool_changed` is no longer reported: an open session
  keeps the tool it started with when the default changes.

- `POST /v1/client/users/{user_uuid}/sessions` requires `consent`
  (`purpose`, `notice_version`, `language`, `consented_at`, optional
  `reference`). `consented_at` must be within the last 15 minutes.
  Requests without it get `422`. Sessions created earlier are
  unaffected and report `consent: null`.

### Added

- Sign-in flows can set `authorization.steps_as_user`: their steps call
  the provider with the signed-in user's access token, so a flow reads
  only that user's data (Graph `/me` with delegated `User.Read`) and
  needs no app-wide permission. The token is never stored or logged.
- `docs/examples/microsoft-entra-id-tool.json` and
  `docs/integrations/microsoft-entra-id.md`: Entra ID sign-in that reads
  the holder's own profile.
- Connection URLs take a tool code or id:
  `/v1/client/users/{user_uuid}/connections/surepass`. A tool serving
  several of the client's types needs `?integration_type=`. Type codes
  in the URL still work.
- `?tool_code=` on every `/v1/client/users/{user_uuid}/connections/
  {type}` route, so a user can connect to several tools of one type;
  the default tool when omitted.
- Several tools per integration type for a client. The routing call
  takes `is_default` (default `true`); `false` adds a tool without
  changing the default. Its response lists every configured tool in
  `tools`.
- `tool_code` on `POST /v1/client/users/{user_uuid}/sessions` and on
  `GET /v1/client/integration-types/{type}/flows`, to use a tool other
  than the default.
- `settings` in the tool drawer (`PUT`/`GET /v1/client/integrations/
  {tool_code}`).
- The user integrations listing shows every usable tool of an enabled
  type, with `is_default` marking the default.
- `PATCH /v1/integration-tools/{tool_code}`: change only the fields
  sent (for example just `logo` or `is_active`); everything else keeps
  its value.
- `GET /v1/client/integration-types/{type}/flows`: flows of the
  client's tool with input specs, kind (`input` or `redirect`),
  outputs, and requestable field ids.
- Sign-in flows (OpenID Connect authorization code with PKCE):
  `authorization` on a flow, status `awaiting_redirect`,
  `next_action` on the start response, and
  `POST /v1/client/sessions/authorization-callback`.
- `input_specs` on flows; inputs that break them get
  `422 invalid_inputs`.
- `requested_attributes` and `requested_fields` on session start;
  `fields` on the result and `result_fields` on the session.
- `DELETE /v1/client/sessions/{id}/result` and
  `POST /v1/client/sessions/{id}/consent-withdrawal`.
- `consent` on session responses.

### Changed

- `410 result_expired` now also covers results erased by the client or
  deleted because consent was withdrawn.
