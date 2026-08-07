## ADDED Requirements

### Requirement: User dataclass includes is_admin field
The `User` dataclass in `src/utils/user_service.py` SHALL include an `is_admin: bool` field defaulting to `False`.

#### Scenario: User object created from DB row with is_admin
- **WHEN** `get_user_by_api_token()` returns a user whose DB row has `is_admin = TRUE`
- **THEN** the returned `User` object SHALL have `is_admin == True`

#### Scenario: User object created from DB row without is_admin
- **WHEN** `get_user_by_api_token()` returns a user whose DB row has `is_admin = FALSE`
- **THEN** the returned `User` object SHALL have `is_admin == False`

#### Scenario: Backward compatibility
- **WHEN** a `User` object is constructed without providing `is_admin`
- **THEN** the field SHALL default to `False`

### Requirement: get_user_by_api_token queries is_admin
The `get_user_by_api_token()` method SHALL include `is_admin` in its SELECT query and map it to the `User` dataclass field.

#### Scenario: SELECT includes is_admin column
- **WHEN** `get_user_by_api_token()` queries the `users` table
- **THEN** the SQL SELECT list SHALL include `is_admin`

### Requirement: get_user queries is_admin
The `get_user()` method SHALL include `is_admin` in its SELECT query and map it to the `User` dataclass field.

#### Scenario: SELECT includes is_admin column
- **WHEN** `get_user()` queries the `users` table
- **THEN** the SQL SELECT list SHALL include `is_admin`

### Requirement: require_bearer_auth uses is_admin for role resolution
The `require_bearer_auth` decorator SHALL assign `["admin"]` roles when `user.is_admin` is `True`, and `[registry.default_role]` otherwise.

#### Scenario: Admin user authenticates via /v1
- **WHEN** a user with `is_admin = TRUE` authenticates with a valid bearer token
- **THEN** the decorator SHALL assign roles `["admin"]`
- **AND** permission checks SHALL use the admin role

#### Scenario: Non-admin user authenticates via /v1
- **WHEN** a user with `is_admin = FALSE` authenticates with a valid bearer token
- **THEN** the decorator SHALL assign roles `[registry.default_role]`
