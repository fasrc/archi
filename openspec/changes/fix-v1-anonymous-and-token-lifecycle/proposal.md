## Why

Two medium-severity compliance gaps remain in the `/v1` OpenAI-compatible API after the critical role-assignment and audit-logging fixes:

1. **`allow_anonymous` ignored**: When `sso.allow_anonymous` is `true` in config, the main app permits unauthenticated access. The `/v1` endpoint ignores this setting entirely — it always rejects requests when `auth_enabled` is true, even if anonymous access is allowed.

2. **Token lifecycle has no expiry or revocation audit**: API tokens live forever until manually revoked. There's no `token_expires_at` column, no expiry check on validation, and `revoke_api_token()` produces no audit trail.

## What Changes

- `require_bearer_auth` in `openai_compat.py` will check `registry.allow_anonymous` and permit unauthenticated requests when anonymous access is enabled, assigning `default_role` permissions.
- Add `api_token_created_at` column to the `users` table and a configurable token TTL (default: 90 days). `get_user_by_api_token()` will reject expired tokens.
- `generate_api_token()` will record creation timestamp. `revoke_api_token()` will call `log_authentication_event()` for audit.

## Capabilities

### New Capabilities
- `v1-anonymous-access`: Respect `allow_anonymous` config in /v1 bearer auth, allowing unauthenticated requests with default-role permissions when enabled.
- `v1-token-expiry`: Token creation timestamps, configurable TTL, expiry checks on validation, and revocation audit logging.

### Modified Capabilities

(none)

## Impact

- `src/interfaces/chat_app/openai_compat.py` — `require_bearer_auth` modified for anonymous access
- `src/utils/user_service.py` — `generate_api_token()`, `get_user_by_api_token()`, `revoke_api_token()` updated
- `src/cli/templates/init.sql` — `api_token_created_at TIMESTAMPTZ` added to users table
- `tests/unit/test_openai_compat_endpoints.py` — new anonymous access tests
- `tests/unit/test_api_tokens.py` — token expiry and revocation audit tests
