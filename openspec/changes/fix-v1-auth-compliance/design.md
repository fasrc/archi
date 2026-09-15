## Context

The `/v1` OpenAI-compatible API uses `require_bearer_auth` to authenticate requests via bearer tokens. Two issues exist:

1. **Role assignment bug**: The decorator checks `getattr(user, "is_admin", False)` (openai_compat.py:90), but the `User` dataclass (user_service.py:35-53) has no `is_admin` field. The column exists in the `users` table (init.sql:43) but is never queried by `get_user_by_api_token()` (user_service.py:510-517). Result: every /v1 user falls back to `registry.default_role`.

2. **Missing audit logging**: The main app calls `log_authentication_event()`, `log_role_assignment()`, and `log_permission_check()` from `src/utils/rbac/audit.py` at every auth touchpoint. The /v1 auth path calls none of these.

## Goals / Non-Goals

**Goals:**
- /v1 role assignment resolves correctly using the `is_admin` column already in the DB
- /v1 authentication events (success, failure, permission denial) produce audit log entries using existing `src/utils/rbac/audit.py` utilities
- User dataclass and `get_user_by_api_token` include `is_admin` so the decorator can use it

**Non-Goals:**
- Adding a full `roles` JSON column or stored role list — the existing `is_admin` boolean is sufficient for the current two-tier model (admin vs default role)
- Changing the RBAC registry or permission model
- Adding token expiry or rotation (separate concern)
- Modifying the SSO/session auth flow

## Decisions

### 1. Use existing `is_admin` column rather than adding a roles column

**Rationale**: The `users.is_admin` column already exists in the DB (init.sql:43). The main app's SSO flow uses JWT role extraction because it deals with arbitrary OIDC claims, but for token-based API auth the admin/default distinction is what matters. Adding a full roles column would require migration tooling and a way to assign roles to API users — out of scope.

**Alternative considered**: Adding a `roles TEXT[]` or `roles JSONB` column. Rejected because it introduces a new role persistence model that diverges from how the main app handles roles (session-based from JWT) and requires deciding who/how roles get assigned to API-token users.

### 2. Extend User dataclass with `is_admin` field

**Rationale**: The field exists in the DB but isn't queried or mapped. Adding it to the dataclass and the SELECT queries is the minimal fix. All existing code that constructs User objects will use the default (`False`), so this is backward compatible.

### 3. Call existing audit utilities inline in `require_bearer_auth`

**Rationale**: The audit functions in `src/utils/rbac/audit.py` are pure logging functions with simple signatures. Calling them directly in the decorator (not via a new abstraction) is the simplest approach and matches how the main app uses them in `app.py`.

**Calls to add**:
- `log_authentication_event(user, "api_token_auth", success, "bearer_token")` on success and failure
- `log_role_assignment(user, roles, "database")` after role resolution
- `log_permission_check(user, permission, granted, endpoint, roles)` on permission check

## Risks / Trade-offs

- **[Risk] `is_admin` is a boolean, not a role list** — If archi later needs more than two tiers for API users, `is_admin` won't suffice. Mitigation: this is a known limitation scoped as a non-goal; a roles column can be added later.
- **[Risk] Audit logging adds latency per request** — The audit functions are synchronous logger calls. Mitigation: negligible; these are local log writes, not DB operations.
- **[Risk] Existing users have `is_admin = FALSE`** — All current API-token users will continue getting `default_role`, which is the same behavior as before. No regression. Admins must explicitly set `is_admin = TRUE` in the DB for elevated access.
