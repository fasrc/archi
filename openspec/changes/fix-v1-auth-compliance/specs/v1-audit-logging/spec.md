## ADDED Requirements

### Requirement: Successful token authentication is logged
The `/v1` auth path SHALL call `log_authentication_event()` on successful bearer token validation.

#### Scenario: Valid token produces audit log entry
- **WHEN** a request with a valid bearer token is authenticated
- **THEN** `log_authentication_event()` SHALL be called with `event_type="api_token_auth"`, `success=True`, `method="bearer_token"`, and the user's identifier

### Requirement: Failed token authentication is logged
The `/v1` auth path SHALL call `log_authentication_event()` on authentication failures.

#### Scenario: Invalid token produces audit log entry
- **WHEN** a request with an invalid bearer token is rejected
- **THEN** `log_authentication_event()` SHALL be called with `event_type="api_token_auth"`, `success=False`, `method="bearer_token"`

#### Scenario: Missing token produces audit log entry
- **WHEN** a request with no Authorization header is rejected (auth enabled)
- **THEN** `log_authentication_event()` SHALL be called with `event_type="api_token_auth"`, `success=False`, `method="bearer_token"`, and details indicating missing token

### Requirement: Role assignment is logged
The `/v1` auth path SHALL call `log_role_assignment()` after resolving user roles.

#### Scenario: Role assignment audit entry
- **WHEN** a user is successfully authenticated via bearer token
- **THEN** `log_role_assignment()` SHALL be called with the user identifier, assigned roles, and `source="database"`

### Requirement: Permission denial is logged
The `/v1` auth path SHALL call `log_permission_check()` when a permission check is performed.

#### Scenario: Permission granted produces audit entry
- **WHEN** an authenticated user has the required `Chat.QUERY` permission
- **THEN** `log_permission_check()` SHALL be called with `granted=True`

#### Scenario: Permission denied produces audit entry
- **WHEN** an authenticated user lacks the required `Chat.QUERY` permission
- **THEN** `log_permission_check()` SHALL be called with `granted=False` and the missing permission listed
