## Why

The Slack service (#510) answers through the chat app's `/v1` API, and it stops at start-up when `/v1` does not answer. The GPU host (`dev`, the only production deployment) has never turned `/v1` on: its chat authentication is off, so `/v1` there would be an unauthenticated model API on port 7861 for anyone who can reach the host. That 404 is deliberate.

The operator decided (2026-10-08) to run the Slack bot on dev with `/v1` reachable only from the host itself. dev deploys in host mode, so the bot calls `http://localhost:7861` and its requests arrive from loopback.

## What Changes

- New setting `services.chat_app.openai_compat.local_only` (default `false`). When `true`, a `before_request` gate on the `/v1` blueprint answers HTTP 403 (`permission_error`) to any request whose TCP peer is not a loopback address, or that carries a proxy forwarding header (`Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Real-IP`). The gate runs before authentication and validation. Other chat-app routes are not affected.
- `openai_compat_options(config)` in `openai_compat.py` reads the settings that `app.py` passes to `register_openai_compat`, so the wiring is unit-tested (`app.py` stays a thin call site).
- `base-config.yaml` renders `local_only` into the stored config.
- Docs: `api-reference-v1.md` "Local callers only"; the Slack prerequisites and `configuration.md` name it.

## Impact

- No change for a deployment that does not set `local_only` (claw keeps its open `/v1`).
- `local_only: true` alone turns `/v1` on (`openai_compat_wanted`), so a config that sets only `local_only` keeps `/v1` off on an older chat app (fails closed).
- dev: `environments/dev.yaml` (fasrc/archi-config) gets `openai_compat: {local_only: true}` (no `enabled`) in a separate PR.
- Files: `src/interfaces/chat_app/openai_compat.py`, `src/interfaces/chat_app/app.py`, `src/cli/templates/base-config.yaml`, `tests/unit/test_openai_compat_local_only.py`, three docs pages.
