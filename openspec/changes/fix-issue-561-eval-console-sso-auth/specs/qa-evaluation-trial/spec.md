## ADDED Requirements

### Requirement: Evaluation console authorization answers anonymous callers like the main app

With auth enabled, the evaluation console's `authorize_request` SHALL accept a logged-in session (basic or SSO) that holds the route's permission, SHALL answer an anonymous API request with a JSON 401, and SHALL redirect an anonymous browser request to the `login` endpoint. On an SSO deployment that blocks anonymous access it SHALL audit each anonymous request as an `anonymous_redirect` authentication event. A caller that supplies no predicates SHALL get a JSON 401 for every anonymous request, as before. The console SHALL NOT accept a bearer token.

#### Scenario: SSO session with VIEW is accepted

- **WHEN** auth is on and the session has `logged_in` true, `auth_method` `sso`, and roles that grant `evaluations:view`
- **THEN** `GET /api/evaluations/catalog` is allowed to proceed

#### Scenario: Anonymous browser is sent to the login page

- **WHEN** auth is on, the predicates are supplied, no session exists, and the request is not an API request
- **THEN** the answer is a redirect to the `login` endpoint, not a 401

#### Scenario: Anonymous API client gets 401

- **WHEN** auth is on, the predicates are supplied, no session exists, and the request is an API request
- **THEN** the answer is a JSON 401 with `error` `Unauthorized`, including on an SSO deployment that blocks anonymous access

#### Scenario: SSO deployment audits the anonymous request

- **WHEN** auth is on, SSO is on, `allow_anonymous` is false, and no session exists
- **THEN** one `anonymous_redirect` authentication event is logged with the request path and method

#### Scenario: Session without the permission gets 403

- **WHEN** auth is on and the session is logged in but its roles do not grant the route's permission
- **THEN** the answer is a JSON 403 that names the required permission

#### Scenario: No predicates keeps the fail-closed default

- **WHEN** `build_authorize_request(True)` is called with no predicates and an anonymous request arrives on any console route
- **THEN** the answer is the JSON 401, with no redirect and no audit event
