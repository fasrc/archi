## ADDED Requirements

### Requirement: extract_roles_from_token is tested
Unit tests SHALL verify JWT role extraction from various token structures.

#### Scenario: Roles in resource_access of decoded token
- **WHEN** a decoded token dict has `resource_access.<app_name>.roles: ['admin', 'user']`
- **THEN** `extract_roles_from_token(token)` SHALL return `['admin', 'user']`

#### Scenario: No resource_access claim
- **WHEN** a token dict has no `resource_access` key
- **THEN** `extract_roles_from_token(token)` SHALL return `[]`

#### Scenario: App not in resource_access
- **WHEN** `resource_access` exists but does not contain the configured `app_name`
- **THEN** `extract_roles_from_token(token)` SHALL return `[]`

#### Scenario: Roles in id_token fallback
- **WHEN** `resource_access` is empty in `access_token` but present in `id_token`
- **THEN** `extract_roles_from_token(token)` SHALL extract roles from the `id_token`

### Requirement: get_user_roles validates and falls back to default
Unit tests SHALL verify role validation and default assignment.

#### Scenario: Valid JWT roles
- **WHEN** a token contains roles that are configured in the registry
- **THEN** `get_user_roles(token, email)` SHALL return only the valid roles

#### Scenario: No valid roles triggers default
- **WHEN** a token contains only roles not configured in the registry
- **THEN** `get_user_roles(token, email)` SHALL return `[registry.default_role]`

### Requirement: require_permission decorator is tested
Unit tests SHALL verify the `@require_permission` decorator using a Flask test client.

#### Scenario: Authenticated user with permission
- **WHEN** a user with session roles that include the required permission accesses a protected route
- **THEN** the response SHALL be 200

#### Scenario: Authenticated user without permission
- **WHEN** a user with session roles that lack the required permission accesses a protected route
- **THEN** the response SHALL be 403
- **AND** the JSON body SHALL include `required_permissions`

#### Scenario: Unauthenticated API request
- **WHEN** no session exists and the request has `Content-Type: application/json`
- **THEN** the response SHALL be 401

### Requirement: require_any_permission decorator is tested
Unit tests SHALL verify the `@require_any_permission` decorator.

#### Scenario: User has one of the listed permissions
- **WHEN** a user has `config:view` and the decorator requires `['config:view', 'config:modify']`
- **THEN** the response SHALL be 200

#### Scenario: User has none of the listed permissions
- **WHEN** a user has only `chat:query` and the decorator requires `['config:view', 'config:modify']`
- **THEN** the response SHALL be 403

### Requirement: check_sso_required decorator is tested
Unit tests SHALL verify the `@check_sso_required` decorator.

#### Scenario: Anonymous allowed
- **WHEN** `allow_anonymous` is `True` and no session exists
- **THEN** the request SHALL proceed (200)

#### Scenario: Anonymous not allowed
- **WHEN** `allow_anonymous` is `False` and no session exists and request is JSON
- **THEN** the response SHALL be 401
