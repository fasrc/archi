## 1. Anonymous Access in /v1

- [x] 1.1 In `require_bearer_auth` (openai_compat.py), when auth is enabled and no token is provided, check `registry.allow_anonymous` before rejecting — if true, set `g.v1_user = None`, assign `[registry.default_role]` roles, and proceed
- [x] 1.2 Add `log_authentication_event()` call for anonymous access with `event_type="api_anonymous_access"`
- [x] 1.3 Add test: anonymous request succeeds when `allow_anonymous` is true
- [x] 1.4 Add test: anonymous request rejected when `allow_anonymous` is false
- [x] 1.5 Add test: valid token still works when `allow_anonymous` is true

## 2. Token Expiry Schema

- [x] 2.1 Add `api_token_created_at TIMESTAMPTZ` column to users table in `src/cli/templates/init.sql`
- [x] 2.2 Update `generate_api_token()` in `user_service.py` to SET `api_token_created_at = NOW()` alongside the hash

## 3. Token Expiry Validation

- [x] 3.1 Add `token_ttl_days` config support — read from openai_compat config or default to 90
- [x] 3.2 Update `get_user_by_api_token()` to SELECT `api_token_created_at` and reject tokens older than TTL (treat NULL as non-expiring)
- [x] 3.3 Add test: token within TTL is accepted
- [x] 3.4 Add test: token beyond TTL is rejected
- [x] 3.5 Add test: token with NULL created_at is accepted (backward compat)

## 4. Revocation Audit

- [x] 4.1 Import `log_authentication_event` in `user_service.py` and call it in `revoke_api_token()` on successful revocation
- [x] 4.2 Add test: revocation logs audit event
- [x] 4.3 Run full test suite and verify all pass with LSP pyright clean
