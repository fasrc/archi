## ADDED Requirements

### Requirement: Local callers only
When `services.chat_app.openai_compat.local_only` is true, every `/v1` route SHALL answer HTTP 403 with an OpenAI-format `permission_error` to a request whose TCP peer is not a loopback address or that carries a `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`, or `X-Real-IP` header.
The check SHALL run before authentication and before request validation, and SHALL NOT
affect routes outside `/v1`. When `local_only` is false or absent, `/v1` SHALL behave as
before.

#### Scenario: A remote caller
- **WHEN** `local_only` is true and `GET /v1/models` arrives from `10.31.4.7`
- **THEN** the response is 403 with `error.type` `permission_error`

#### Scenario: The Slack bot on the same host
- **WHEN** `local_only` is true and `GET /v1/models` arrives from `127.0.0.1` with no forwarding header
- **THEN** the request is answered as without `local_only`

#### Scenario: A same-host proxy
- **WHEN** `local_only` is true and a request arrives from `127.0.0.1` with `X-Forwarded-For`
- **THEN** the response is 403

#### Scenario: Auth on, remote caller, no token
- **WHEN** `local_only` and authentication are both on and a remote caller sends no token
- **THEN** the response is 403, not 401

### Requirement: local_only alone turns /v1 on
The chat app SHALL register `/v1` when `services.chat_app.openai_compat.enabled` or `services.chat_app.openai_compat.local_only` is true, so a config that sets only `local_only` keeps `/v1` off on a chat app that predates `local_only`.

#### Scenario: Local-only config on new code
- **WHEN** the config sets `openai_compat.local_only: true` and no `enabled`
- **THEN** `/v1` is registered and answers loopback callers only

#### Scenario: Local-only config on an older chat app
- **WHEN** an older chat app, which reads only `enabled`, loads a config that sets only `local_only: true`
- **THEN** `/v1` is not registered

### Requirement: The setting reaches the running app
`base-config.yaml` SHALL render `services.chat_app.openai_compat.local_only` as a boolean (absent or null → `false`), and `app.py` SHALL pass it to `register_openai_compat` through `openai_compat_options`.

#### Scenario: Rendered config
- **WHEN** a deployment config sets `openai_compat.local_only: true`
- **THEN** the rendered config has `local_only: true`, and the registered blueprint refuses a remote caller
