## Why

The `/v1` OpenAI-compatible API bypasses archi's established authentication patterns in two ways: role assignment always falls back to `default_role` because it checks a non-existent `is_admin` attribute, and token-based authentication produces zero audit log entries. This means admins cannot differentiate API user permissions and have no security audit trail for programmatic access.

## What Changes

- Fix role assignment in `require_bearer_auth` to use stored user roles from the database instead of a non-existent `is_admin` flag.
- Add audit logging for /v1 authentication events (success, failure, permission denial) using the existing `log_authentication_event()` and `log_permission_check()` utilities from `src/utils/rbac/audit.py`.
- Add a `roles` field to the User model and persist roles during user creation so token-based auth can resolve roles without a JWT.

## Capabilities

### New Capabilities
- `v1-role-assignment`: Correct role resolution for /v1 bearer-token authenticated users, using stored roles from the database.
- `v1-audit-logging`: Audit trail for /v1 authentication attempts, token validations, and permission checks using existing RBAC audit utilities.

### Modified Capabilities

(none)

## Impact

- `src/interfaces/chat_app/openai_compat.py` — `require_bearer_auth` decorator modified
- `src/utils/user_service.py` — User model extended with `roles` field; `get_or_create_user()` updated to accept and persist roles
- `src/utils/rbac/audit.py` — no changes, used as-is
- `src/cli/templates/init.sql` — `users` table may need a `roles` column
- `tests/unit/test_openai_compat_endpoints.py` — tests updated to verify role assignment and audit logging
