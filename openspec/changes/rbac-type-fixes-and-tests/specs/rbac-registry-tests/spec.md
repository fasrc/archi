## ADDED Requirements

### Requirement: RBACRegistry permission resolution is tested
Unit tests SHALL verify that `RBACRegistry.has_permission()` correctly resolves permissions through inheritance chains and wildcard grants.

#### Scenario: Direct permission grant
- **WHEN** a role has `chat:query` in its permissions list
- **THEN** `has_permission([role], 'chat:query')` SHALL return `True`

#### Scenario: Permission via inheritance
- **WHEN** role A inherits from role B, and role B has `upload:documents`
- **THEN** `has_permission([A], 'upload:documents')` SHALL return `True`

#### Scenario: Multi-level inheritance
- **WHEN** role A inherits B, B inherits C, and C has `config:view`
- **THEN** `has_permission([A], 'config:view')` SHALL return `True`

#### Scenario: Wildcard grants all permissions
- **WHEN** a role has `*` in its permissions
- **THEN** `has_permission([role], 'any:permission')` SHALL return `True`

#### Scenario: Permission not granted
- **WHEN** a role does not have `upload:documents` and does not inherit it
- **THEN** `has_permission([role], 'upload:documents')` SHALL return `False`

### Requirement: RBACRegistry config validation is tested
Unit tests SHALL verify that invalid configurations are rejected at construction time.

#### Scenario: Circular inheritance detected
- **WHEN** a config defines role A inheriting B and B inheriting A
- **THEN** `RBACRegistry(config)` SHALL raise `RBACConfigError`

#### Scenario: No roles defined
- **WHEN** a config has an empty `roles` dict
- **THEN** `RBACRegistry(config)` SHALL raise `RBACConfigError`

#### Scenario: Inheritance from undefined role
- **WHEN** a role inherits from a role not defined in the config
- **THEN** `RBACRegistry(config)` SHALL raise `RBACConfigError`

### Requirement: RBACRegistry role filtering is tested
Unit tests SHALL verify that `filter_valid_roles()` correctly strips unknown roles.

#### Scenario: Mixed valid and invalid roles
- **WHEN** `filter_valid_roles(['admin', 'unknown-role', 'base-user'])` is called
- **THEN** the result SHALL be `['admin', 'base-user']` (only configured roles)

#### Scenario: All roles invalid
- **WHEN** `filter_valid_roles(['foo', 'bar'])` is called with no matching configured roles
- **THEN** the result SHALL be `[]`

### Requirement: RBACRegistry properties are tested
Unit tests SHALL verify `default_role`, `allow_anonymous`, and `app_name` properties.

#### Scenario: default_role from config
- **WHEN** config sets `default_role: 'viewer'`
- **THEN** `registry.default_role` SHALL return `'viewer'`

#### Scenario: allow_anonymous from sso config
- **WHEN** config sets `sso.allow_anonymous: true`
- **THEN** `registry.allow_anonymous` SHALL return `True`

#### Scenario: app_name override via constructor
- **WHEN** `RBACRegistry(config, app_name='my-app')` is constructed
- **THEN** `registry.app_name` SHALL return `'my-app'`
