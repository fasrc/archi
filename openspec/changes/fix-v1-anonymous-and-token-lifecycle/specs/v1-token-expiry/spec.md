## ADDED Requirements

### Requirement: Token creation timestamp is recorded
`generate_api_token()` SHALL record the token creation time in the `api_token_created_at` column.

#### Scenario: New token gets creation timestamp
- **WHEN** a user generates a new API token
- **THEN** `api_token_created_at` SHALL be set to the current UTC time

### Requirement: Expired tokens are rejected
`get_user_by_api_token()` SHALL reject tokens older than the configured TTL.

#### Scenario: Valid token within TTL
- **WHEN** a token is presented whose `api_token_created_at` is within the configured TTL
- **THEN** the token SHALL be accepted and the user returned

#### Scenario: Expired token beyond TTL
- **WHEN** a token is presented whose `api_token_created_at` is older than the configured TTL
- **THEN** the token SHALL be rejected (return None)
- **AND** a warning SHALL be logged indicating token expiry

#### Scenario: Token with NULL created_at (pre-existing)
- **WHEN** a token is presented with `api_token_created_at = NULL` (created before this change)
- **THEN** the token SHALL be accepted (treated as non-expiring)

### Requirement: Token revocation is audited
`revoke_api_token()` SHALL produce an audit log entry.

#### Scenario: Successful revocation logs audit event
- **WHEN** a user's API token is revoked
- **THEN** `log_authentication_event()` SHALL be called with `event_type="api_token_revoke"`, `success=True`, `method="bearer_token"`

#### Scenario: Revocation of non-existent token
- **WHEN** revocation is attempted but no token exists
- **THEN** no audit event SHALL be logged (nothing was revoked)

### Requirement: Schema includes api_token_created_at column
The `users` table SHALL include an `api_token_created_at TIMESTAMPTZ` column.

#### Scenario: Column exists in init.sql
- **WHEN** the database is initialized from `init.sql`
- **THEN** the `users` table SHALL have an `api_token_created_at` column of type `TIMESTAMPTZ`
