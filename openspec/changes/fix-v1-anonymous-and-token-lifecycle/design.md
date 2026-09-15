## Context

Two remaining compliance gaps in `/v1` auth:

1. `require_bearer_auth` (openai_compat.py:64-112) checks only `_auth_enabled`. It never consults `registry.allow_anonymous` (registry.py:204-206), so unauthenticated /v1 requests are always rejected even when the config permits anonymous access.

2. API tokens have no lifecycle management. The `users` table (init.sql:65) stores only `api_token_hash`—no creation timestamp or expiry. `generate_api_token()` (user_service.py:458-496) doesn't record when the token was created. `get_user_by_api_token()` (user_service.py:498-544) doesn't check age. `revoke_api_token()` (user_service.py:568-593) doesn't log audit events.

## Goals / Non-Goals

**Goals:**
- /v1 respects `allow_anonymous` from RBAC registry, permitting unauthenticated access with default-role permissions when enabled
- API tokens have a creation timestamp and configurable TTL (default 90 days)
- Expired tokens are rejected with a clear error message
- Token revocation produces an audit log entry

**Non-Goals:**
- Token refresh/rotation mechanism (can be added later)
- Per-user TTL overrides (use the global default)
- Changing the main app's session auth flow
- Adding anonymous access to the /v1 token generation endpoint

## Decisions

### 1. Anonymous /v1 access assigns default_role, not unrestricted access

When `allow_anonymous` is true and no bearer token is provided, the request proceeds with `g.v1_user = None` and roles set to `[registry.default_role]`. Permission checks still apply—anonymous users can only do what `default_role` permits.

**Alternative considered**: Skip permission checks entirely for anonymous. Rejected because it breaks the RBAC model and could expose privileged operations.

### 2. Token TTL stored as config, not per-token column

Add `api_token_created_at TIMESTAMPTZ` to the `users` table. TTL is read from config (`services.chat_app.openai_compat.token_ttl_days`, default 90). Expiry is checked at validation time: `NOW() - api_token_created_at > TTL`.

**Alternative considered**: Per-token `expires_at` column. Rejected because it adds complexity (users choosing expiry) and we only have one token per user. A global TTL is simpler and covers the security concern.

### 3. Revocation audit uses existing log_authentication_event

`revoke_api_token()` in `user_service.py` will call `log_authentication_event(user, "api_token_revoke", success=True, method="bearer_token")` after nullifying the hash. This reuses the existing audit infrastructure.

## Risks / Trade-offs

- **[Risk] Existing tokens have NULL `api_token_created_at`** — Tokens created before this change have no timestamp. Mitigation: treat NULL as "created now" on first validation (set via UPDATE), or treat as never-expiring. Design choice: treat NULL as non-expiring to avoid breaking existing tokens. Users can regenerate to get expiry.
- **[Risk] Anonymous access widens the attack surface** — Mitigation: anonymous access is opt-in via config (`allow_anonymous` defaults to false), and anonymous users still go through RBAC permission checks.
- **[Risk] 90-day default TTL may surprise users** — Mitigation: log a warning when a token is near expiry (last 7 days). Document the TTL in the API reference.
