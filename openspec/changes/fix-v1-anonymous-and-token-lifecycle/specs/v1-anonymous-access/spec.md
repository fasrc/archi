## ADDED Requirements

### Requirement: /v1 respects allow_anonymous config
When `auth_enabled` is true and `registry.allow_anonymous` is true, `require_bearer_auth` SHALL permit requests without a bearer token, assigning default-role permissions.

#### Scenario: Anonymous request when allow_anonymous is true
- **WHEN** auth is enabled, `allow_anonymous` is true, and a request has no Authorization header
- **THEN** the request SHALL proceed with `g.v1_user = None` and roles set to `[registry.default_role]`
- **AND** permission checks SHALL still apply using the default role

#### Scenario: Anonymous request when allow_anonymous is false
- **WHEN** auth is enabled, `allow_anonymous` is false, and a request has no Authorization header
- **THEN** the request SHALL be rejected with 401

#### Scenario: Valid token still works when allow_anonymous is true
- **WHEN** auth is enabled, `allow_anonymous` is true, and a request provides a valid bearer token
- **THEN** the request SHALL authenticate normally using the token's user identity and roles

### Requirement: Anonymous access is audited
Anonymous /v1 access SHALL produce an audit log entry.

#### Scenario: Anonymous request logs audit event
- **WHEN** an anonymous request is permitted via `allow_anonymous`
- **THEN** `log_authentication_event()` SHALL be called with `user="anonymous"`, `event_type="api_anonymous_access"`, `success=True`, `method="anonymous"`
