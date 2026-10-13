# Changelog

## Unreleased

### Breaking

- `POST /v1/client/users/{user_uuid}/sessions` requires `consent`
  (`purpose`, `notice_version`, `language`, `consented_at`, optional
  `reference`). `consented_at` must be within the last 15 minutes.
  Requests without it get `422`. Sessions created earlier are
  unaffected and report `consent: null`.

### Added

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
