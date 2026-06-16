## ADDED Requirements

### Requirement: Bearer token authentication
The `/v1` endpoints SHALL authenticate requests via `Authorization: Bearer <token>` headers. The token SHALL resolve to an archi user via the existing user service.

#### Scenario: Valid bearer token
- **WHEN** a request includes `Authorization: Bearer <valid-token>`
- **THEN** the system resolves the token to an archi user and processes the request with that user's identity

#### Scenario: Missing authorization header
- **WHEN** a request to a `/v1` endpoint has no `Authorization` header and auth is enabled
- **THEN** the system returns HTTP 401 with `{"error": {"message": "Authentication required", "type": "invalid_request_error"}}`

#### Scenario: Invalid token
- **WHEN** a request includes `Authorization: Bearer <invalid-token>`
- **THEN** the system returns HTTP 401 with `{"error": {"message": "Invalid token", "type": "invalid_request_error"}}`

#### Scenario: Auth disabled
- **WHEN** archi's auth is disabled in configuration
- **THEN** `/v1` endpoints process requests without requiring an Authorization header

---

### Requirement: RBAC enforcement on /v1
The system SHALL enforce archi's existing RBAC permissions on `/v1` requests. A user MUST have the `chat:query` permission to use `/v1/chat/completions`.

#### Scenario: User with chat permission
- **WHEN** an authenticated user with `chat:query` permission sends a chat completions request
- **THEN** the request is processed normally

#### Scenario: User without chat permission
- **WHEN** an authenticated user without `chat:query` permission sends a chat completions request
- **THEN** the system returns HTTP 403 with `{"error": {"message": "Permission denied", "type": "invalid_request_error"}}`

---

### Requirement: Token management
The system SHALL provide a mechanism for users to generate and manage API tokens for `/v1` access. Tokens SHALL be stored as a one-way hash (SHA-256) in the users table.

#### Scenario: Token generation
- **WHEN** a user requests a new API token via archi's native UI or API
- **THEN** the system generates a unique token, stores it encrypted, and returns the plaintext token once

#### Scenario: Token revocation
- **WHEN** a user revokes an API token
- **THEN** subsequent requests using that token return HTTP 401
