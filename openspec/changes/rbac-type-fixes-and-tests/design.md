## Context

The RBAC subsystem (`src/utils/rbac/`) has 6 modules with zero dedicated test coverage and 3 categories of pyright type errors. Our `openwebui-compat-mode` branch adds `/v1` API endpoints that import and call `get_registry()`, `has_permission()`, and the three audit logging functions. Fixing types and adding tests prevents our branch from inheriting latent bugs and gives regression confidence for the permission logic the `/v1` API depends on.

Pre-existing pyright errors:
- `registry.py:148` — mutable default `visited: Set[str] = None` (should be `Optional[Set[str]] = None`)
- `jwt_parser.py:158` — mutable default `original_roles: List[str] = None` (should be `Optional[List[str]] = None`)
- `decorators.py` — 8 call sites pass `request.endpoint` (`Optional[str]`) to `log_permission_check(endpoint: str)` without handling `None`

## Goals / Non-Goals

**Goals:**
- Fix all pyright type errors in RBAC modules so they pass `pyright --pythonversion 3.11`
- Add unit tests for RBACRegistry (inheritance, wildcards, circular detection, validation)
- Add unit tests for permission utilities and audit logging functions our `/v1` code calls
- Add unit tests for JWT role extraction and Flask route decorators
- Structure work in 4 isolated batches that can be applied and verified independently

**Non-Goals:**
- Refactoring RBAC module logic or architecture
- Adding integration tests that require a running Flask app or database
- Fixing any behavioral bugs (only type annotations and missing tests)
- Testing `permission_enum.py` (static enums, tested implicitly)
- Testing `load_rbac_config()` filesystem search logic (fragile, environment-dependent)

## Decisions

### 1. Test file structure: one file per module
Each RBAC module gets its own test file in `tests/unit/`. This keeps test files focused and allows running a single module's tests in isolation.

**Alternative**: Single `test_rbac.py` file — rejected because it would grow to 300+ lines and make it harder to run batch-specific tests.

### 2. Registry tests use plain dict configs, no YAML files
`RBACRegistry.__init__` accepts a `Dict[str, Any]`. Tests construct config dicts inline rather than loading YAML fixtures. This keeps tests self-contained and avoids filesystem dependencies.

### 3. Decorator tests use Flask test client
`decorators.py` wraps Flask endpoints and reads `session`. Tests create a minimal Flask app with `app.test_client()` and set session values directly. This is the standard Flask testing pattern.

### 4. Type fixes are minimal — only fix the pyright errors
- `registry.py`: Change `visited: Set[str] = None` to `visited: Optional[Set[str]] = None`
- `jwt_parser.py`: Change `original_roles: List[str] = None` to `original_roles: Optional[List[str]] = None`
- `decorators.py`: Use `request.endpoint or '<unknown>'` at each call site to coerce `Optional[str]` to `str`

**Alternative for decorators**: Change `log_permission_check` signature to accept `Optional[str]` — rejected because the audit function's contract is correct (endpoint should always be a string in the log entry), and the callers should handle the None case.

### 5. Batch ordering follows dependency priority
Batch 1 (type fixes) comes first because it unblocks clean pyright runs for subsequent batches. Batches 2-3 cover modules our `/v1` code calls directly. Batch 4 covers modules not directly used by `/v1`.

## Risks / Trade-offs

- **[Risk] Decorator type fix changes behavior if `request.endpoint` is actually None** → Mitigation: `'<unknown>'` is a safe fallback for the audit log string. This only affects the log message, not auth decisions.
- **[Risk] Tests may need updating if RBAC modules are refactored later** → Mitigation: Tests are scoped to public API behavior, not internal implementation. They should survive most refactors.
- **[Risk] Batch isolation means temporary partial coverage** → Mitigation: Batches are prioritized by our branch's dependency order. The most critical modules get coverage first.
