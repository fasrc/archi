## 1. Batch 1 — Pyright Type Fixes

- [x] 1.1 In `registry.py:148`, change `visited: Set[str] = None` to `visited: Optional[Set[str]] = None`
- [x] 1.2 In `jwt_parser.py:158`, change `original_roles: List[str] = None` to `original_roles: Optional[List[str]] = None`
- [x] 1.3 In `decorators.py`, fix all 8 call sites passing `request.endpoint` to `log_permission_check()` — use `request.endpoint or '<unknown>'`
- [x] 1.4 Run `pyright` on all 3 files and verify zero new errors

## 2. Batch 2 — Registry Tests

- [x] 2.1 Create `tests/unit/test_rbac_registry.py` with test config fixtures (inline dicts)
- [x] 2.2 Add tests: direct permission grant, permission via inheritance, multi-level inheritance
- [x] 2.3 Add tests: wildcard grants all, permission not granted
- [x] 2.4 Add tests: circular inheritance raises RBACConfigError, no roles raises RBACConfigError, undefined parent raises RBACConfigError
- [x] 2.5 Add tests: filter_valid_roles with mixed valid/invalid, all invalid
- [x] 2.6 Add tests: default_role, allow_anonymous, app_name properties
- [x] 2.7 Run tests and verify all pass with pyright clean

## 3. Batch 3 — Permissions + Audit Tests

- [x] 3.1 Create `tests/unit/test_rbac_permissions.py` with mock registry setup
- [x] 3.2 Add tests: has_permission granted/denied/empty with explicit roles
- [x] 3.3 Add tests: is_admin with wildcard role, non-admin role
- [x] 3.4 Add tests: is_expert via config:modify, via admin, non-expert
- [x] 3.5 Create `tests/unit/test_rbac_audit.py`
- [x] 3.6 Add tests: log_authentication_event success/failure output and log levels
- [x] 3.7 Add tests: log_permission_check granted (debug) / denied (warning + JSON)
- [x] 3.8 Add tests: log_role_assignment jwt (info) / default (warning)
- [x] 3.9 Run tests and verify all pass with pyright clean

## 4. Batch 4 — JWT Parser + Decorator Tests

- [x] 4.1 Create `tests/unit/test_rbac_jwt_parser.py` with mock registry
- [x] 4.2 Add tests: extract_roles_from_token with resource_access, no resource_access, wrong app_name, id_token fallback
- [x] 4.3 Add tests: get_user_roles with valid roles, no valid roles (default fallback)
- [x] 4.4 Create `tests/unit/test_rbac_decorators.py` with Flask test app + client
- [x] 4.5 Add tests: require_permission — authenticated with permission (200), without permission (403), unauthenticated (401)
- [x] 4.6 Add tests: require_any_permission — has one (200), has none (403)
- [x] 4.7 Add tests: check_sso_required — anonymous allowed (200), anonymous not allowed (401)
- [x] 4.8 Run tests and verify all pass with pyright clean
