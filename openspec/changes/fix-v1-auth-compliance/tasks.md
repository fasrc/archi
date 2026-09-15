## 1. User Dataclass & Query Updates

- [x] 1.1 Add `is_admin: bool = False` field to the `User` dataclass in `src/utils/user_service.py`
- [x] 1.2 Update `get_user()` SELECT query to include `is_admin` and map it to the User object
- [x] 1.3 Update `get_user_by_api_token()` SELECT query to include `is_admin` and map it to the User object
- [x] 1.4 Update `get_or_create_user()` RETURNING clause to include `is_admin` and map it

## 2. Audit Logging in require_bearer_auth

- [x] 2.1 Import `log_authentication_event`, `log_role_assignment`, `log_permission_check` from `src/utils/rbac/audit`
- [x] 2.2 Add `log_authentication_event()` call on missing/invalid token (failure cases)
- [x] 2.3 Add `log_authentication_event()` call on successful token validation
- [x] 2.4 Add `log_role_assignment()` call after role resolution
- [x] 2.5 Add `log_permission_check()` call on permission grant/denial

## 3. Tests

- [x] 3.1 Update test fixtures to include `is_admin` field on FakeUser in `tests/unit/test_openai_compat_endpoints.py`
- [x] 3.2 Add test: admin user (is_admin=True) gets admin role via /v1
- [x] 3.3 Add test: non-admin user gets default role via /v1
- [x] 3.4 Add test: successful auth produces audit log entry
- [x] 3.5 Add test: failed auth produces audit log entry
- [x] 3.6 Run full test suite and verify all pass with LSP pyright clean
