## Why

The RBAC subsystem (`src/utils/rbac/`) has pre-existing pyright type errors and zero unit test coverage. Our `openwebui-compat-mode` branch imports and calls several RBAC functions (`get_registry`, `has_permission`, `log_authentication_event`, `log_role_assignment`, `log_permission_check`) from the new `/v1` API code. Without fixing the type issues and adding tests, we risk merging a branch that inherits latent type bugs and has no regression safety net for the permission logic it depends on.

## What Changes

- Fix 3 pyright type-annotation bugs across `registry.py`, `jwt_parser.py`, and `decorators.py`
- Add unit tests for `registry.py` (core permission engine: inheritance, wildcards, validation)
- Add unit tests for `permissions.py` and `audit.py` (convenience functions and logging our `/v1` code calls directly)
- Add unit tests for `jwt_parser.py` and `decorators.py` (SSO role extraction and Flask endpoint protection)

Work is batched in priority order — modules our branch depends on first:
1. **Batch 1**: Pyright type fixes (all 3 files)
2. **Batch 2**: `registry.py` tests (core engine)
3. **Batch 3**: `permissions.py` + `audit.py` tests (functions `/v1` calls)
4. **Batch 4**: `jwt_parser.py` + `decorators.py` tests (not directly used by `/v1`)

## Capabilities

### New Capabilities
- `rbac-type-safety`: Fix pyright type-annotation errors in RBAC modules
- `rbac-registry-tests`: Unit tests for RBACRegistry (inheritance, wildcards, validation, caching)
- `rbac-permissions-and-audit-tests`: Unit tests for permission utilities and audit logging
- `rbac-jwt-and-decorator-tests`: Unit tests for JWT role extraction and Flask route decorators

### Modified Capabilities

## Impact

- `src/utils/rbac/registry.py` — type annotation fix (line 148)
- `src/utils/rbac/jwt_parser.py` — type annotation fix (line 158)
- `src/utils/rbac/decorators.py` — type annotation fixes (8 call sites)
- `tests/unit/test_rbac_registry.py` — new file
- `tests/unit/test_rbac_permissions.py` — new file
- `tests/unit/test_rbac_audit.py` — new file
- `tests/unit/test_rbac_jwt_parser.py` — new file
- `tests/unit/test_rbac_decorators.py` — new file
