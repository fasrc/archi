## ADDED Requirements

### Requirement: Unauthenticated browser requests SHALL receive a login redirect
An unauthenticated request that is not an API request SHALL be answered with a redirect to
the `login` endpoint rather than a JSON error body, for every route guarded by
`FlaskAppWrapper.require_auth` or `FlaskAppWrapper.require_perm`, whenever authentication is enabled and the
caller is not logged in.

#### Scenario: Browser navigation to a chat page
- **WHEN** an unauthenticated browser issues `GET /chat` with authentication enabled and the
  SSO anonymous redirect not applicable
- **THEN** the response status is `302`
- **AND** the `Location` header resolves to the `login` endpoint

#### Scenario: Browser navigation to a permission-guarded page
- **WHEN** an unauthenticated browser issues `GET /upload`, a page guarded by
  `require_perm`
- **THEN** the response status is `302` to the `login` endpoint
- **AND** no `401` JSON body is returned

### Requirement: Unauthenticated API requests SHALL still receive the unchanged 401 body
A request whose path begins with `/api/` or whose content type is JSON SHALL continue to
receive HTTP `401` with the response body `{"error": "Unauthorized", "message":
"Authentication required"}` exactly as before this change, so existing API consumers are
unaffected.

#### Scenario: API path is unaffected
- **WHEN** an unauthenticated caller issues `POST /api/like`
- **THEN** the response status is `401`
- **AND** the JSON body equals `{"error": "Unauthorized", "message": "Authentication required"}`

#### Scenario: A JSON request to a non-API path is treated as an API request
- **WHEN** an unauthenticated caller issues a request to a guarded non-`/api/` path with a
  JSON content type
- **THEN** the response status is `401` and not a redirect

### Requirement: The SSO anonymous redirect and its audit event SHALL be preserved
The existing SSO redirect SHALL still occur and SHALL still emit the `anonymous_redirect`
authentication event, for API and browser callers alike, taking precedence over the
API/browser split, whenever SSO is enabled and the registry does not allow anonymous access.

#### Scenario: SSO enforced for an anonymous caller
- **WHEN** an unauthenticated request arrives with SSO enabled and `allow_anonymous` false
- **THEN** the response is a redirect to the `login` endpoint
- **AND** an authentication event of type `anonymous_redirect` is logged

#### Scenario: SSO enforcement outranks the API split
- **WHEN** that same request targets an `/api/` path
- **THEN** the response is still the SSO redirect, not a `401`

### Requirement: Authenticated and auth-disabled requests SHALL pass through untouched
The decorators SHALL invoke the wrapped route without modification when authentication is
disabled, or when the session is already logged in, so this change alters only the
unauthenticated rejection path.

#### Scenario: Authentication disabled
- **WHEN** `auth_enabled` is false
- **THEN** the wrapped route is invoked and its response returned unchanged

#### Scenario: Session already logged in
- **WHEN** the session has `logged_in` true
- **THEN** the wrapped route is invoked and its response returned unchanged

### Requirement: The authentication decorators SHALL contain no unreachable statement
`FlaskAppWrapper.require_auth` SHALL be free of statements that follow an unconditional `return`
within the same block, so the API/browser split it declares is the split it performs.

#### Scenario: Reachability probe is clean
- **WHEN** the module is parsed and each `decorated_function` body is walked for a statement
  following an unconditional `return` in the same block
- **THEN** no such statement is found
