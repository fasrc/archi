## ADDED Requirements

### Requirement: registry.py passes pyright without type errors
`_resolve_permissions` SHALL use `Optional[Set[str]]` for its `visited` parameter default.

#### Scenario: visited parameter accepts None
- **WHEN** `_resolve_permissions` is called without the `visited` argument
- **THEN** the parameter SHALL default to `None` and be initialized to `set()` inside the method
- **AND** pyright SHALL report no type error on the function signature

### Requirement: jwt_parser.py passes pyright without type errors
`assign_default_role` SHALL use `Optional[List[str]]` for its `original_roles` parameter default.

#### Scenario: original_roles parameter accepts None
- **WHEN** `assign_default_role` is called without the `original_roles` argument
- **THEN** the parameter SHALL default to `None`
- **AND** pyright SHALL report no type error on the function signature

### Requirement: decorators.py passes pyright without type errors
All calls to `log_permission_check()` in decorators SHALL pass a non-None `str` for the `endpoint` parameter.

#### Scenario: request.endpoint is None
- **WHEN** a decorator calls `log_permission_check` and `request.endpoint` is `None`
- **THEN** the decorator SHALL pass a fallback string (e.g., `'<unknown>'`) instead of `None`
- **AND** pyright SHALL report no type error at the call site

#### Scenario: request.endpoint is a valid string
- **WHEN** a decorator calls `log_permission_check` and `request.endpoint` is a non-None string
- **THEN** the decorator SHALL pass that string directly
