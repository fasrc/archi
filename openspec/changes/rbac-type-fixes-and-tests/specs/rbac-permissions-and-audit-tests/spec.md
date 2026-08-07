## ADDED Requirements

### Requirement: has_permission utility is tested with explicit roles
Unit tests SHALL verify `has_permission()` works correctly when roles are passed explicitly (bypassing flask session).

#### Scenario: Permission granted via explicit roles
- **WHEN** `has_permission('chat:query', roles=['base-user'])` is called and `base-user` has `chat:query`
- **THEN** the function SHALL return `True`

#### Scenario: Permission denied via explicit roles
- **WHEN** `has_permission('config:modify', roles=['base-user'])` is called and `base-user` lacks `config:modify`
- **THEN** the function SHALL return `False`

#### Scenario: Empty roles list
- **WHEN** `has_permission('chat:query', roles=[])` is called
- **THEN** the function SHALL return `False`

### Requirement: is_admin utility is tested
Unit tests SHALL verify `is_admin()` detects wildcard permissions.

#### Scenario: Admin role with wildcard
- **WHEN** `is_admin(roles=['admin'])` is called and `admin` has `*` permission
- **THEN** the function SHALL return `True`

#### Scenario: Non-admin role without wildcard
- **WHEN** `is_admin(roles=['base-user'])` is called and `base-user` has only specific permissions
- **THEN** the function SHALL return `False`

### Requirement: is_expert utility is tested
Unit tests SHALL verify `is_expert()` detects expert-level permissions.

#### Scenario: Expert via config:modify
- **WHEN** `is_expert(roles=['power-user'])` is called and `power-user` has `config:modify`
- **THEN** the function SHALL return `True`

#### Scenario: Expert via admin role
- **WHEN** `is_expert(roles=['admin'])` is called and `admin` has `*`
- **THEN** the function SHALL return `True`

#### Scenario: Non-expert
- **WHEN** `is_expert(roles=['base-user'])` is called and `base-user` has only `chat:query`
- **THEN** the function SHALL return `False`

### Requirement: log_authentication_event produces correct output
Unit tests SHALL verify structured audit log entries.

#### Scenario: Successful auth event
- **WHEN** `log_authentication_event('user@test.com', 'login', success=True, method='sso')` is called
- **THEN** the audit logger SHALL be called with a message containing `AUTH | login | user@test.com | SUCCESS | method: sso`

#### Scenario: Failed auth event
- **WHEN** `log_authentication_event('unknown', 'api_token_auth', success=False, method='bearer_token', details='No token')` is called
- **THEN** the audit logger SHALL log at warning level with a message containing `FAILURE`

### Requirement: log_permission_check produces correct output
Unit tests SHALL verify permission check audit entries.

#### Scenario: Granted permission log
- **WHEN** `log_permission_check('user@test.com', 'chat:query', granted=True, endpoint='/chat', roles=['base-user'])` is called
- **THEN** the audit logger SHALL log at debug level

#### Scenario: Denied permission log
- **WHEN** `log_permission_check('user@test.com', 'config:modify', granted=False, endpoint='/config', roles=['base-user'], missing=['config:modify'])` is called
- **THEN** the audit logger SHALL log at warning level
- **AND** the structured JSON entry SHALL include `missing_permissions`

### Requirement: log_role_assignment produces correct output
Unit tests SHALL verify role assignment audit entries.

#### Scenario: JWT role assignment
- **WHEN** `log_role_assignment('user@test.com', roles=['admin'], source='jwt', is_default=False)` is called
- **THEN** the audit logger SHALL log at info level with source `jwt`

#### Scenario: Default role assignment
- **WHEN** `log_role_assignment('user@test.com', roles=['base-user'], source='default', is_default=True)` is called
- **THEN** the audit logger SHALL log at warning level
