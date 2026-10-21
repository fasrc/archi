## Why

The QA evaluation console answers every caller that is not logged in with a flat
JSON 401, browser or API client alike (`build_authorize_request`,
`src/interfaces/chat_app/evaluation_console.py:165-218`). The main chat app
answers the same caller differently: a browser goes to the login page, an API
client gets a parseable 401, and an SSO deployment that blocks anonymous access
audits the redirect (`require_auth` / `require_perm`, `src/interfaces/chat_app/app.py:3569`
and `:3616`). So an auth-on deployment that turns the console on shows a raw JSON
error to a person who opens `/evaluations` in a browser, where the rest of the app
sends that person to sign in. The module docstring (`:13-17`) records this as a
trial divergence and an adoption precondition for #320.

Measured at `origin/dev` `e58a7ada`: with `build_authorize_request(True)`, an
anonymous `GET /evaluations` from a browser returns `401`, not a redirect.

Child 1 of #320 (decision recorded on #561: option B(i), inject predicates, no
bearer auth).

## What Changes

- `build_authorize_request(auth_enabled, *, sso_enabled=..., allow_anonymous=...,
  is_api_request=...)` gains three injected zero-argument predicates. The defaults
  keep today's behaviour exactly: a caller that passes no predicates still gets a
  JSON 401 for every anonymous request.
- With the predicates supplied, an anonymous request is answered the way the main
  app answers it: API request → JSON 401; browser request → redirect to the
  `login` endpoint; on an SSO deployment with `allow_anonymous` false, the redirect
  is audited with `log_authentication_event(... event_type="anonymous_redirect")`.
- A logged-in session — including one an SSO login created — is checked for the
  route's permission as today: permitted → proceed, not permitted → JSON 403.
- The one `app.py` call site passes the main app's own predicates.
- No bearer-token path is added.
- The module and function docstrings drop the "no SSO branch" divergence text.
- `docs/docs/configuration.md` states which `auth_roles` permissions open the
  console (`evaluations:view`, `evaluations:run`, `evaluations:manage`, or `*`).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `qa-evaluation-trial`: adds a requirement that the console's authorization
  answers anonymous browsers and API clients like the main app.

## Impact

- `src/interfaces/chat_app/evaluation_console.py` (the seam),
  `src/interfaces/chat_app/app.py` (one call site),
  `tests/unit/test_evaluation_console.py`, `docs/docs/configuration.md`.
- No config template change, no new dependency, no deploy change. The console
  stays off by default; the dev flip stays on #320.

## Not in scope

- Bearer-token authentication (a separate decision if ever needed).
- Redaction (#562), the viewer split (#563), the CLI rename (#564), the dev flip (#320).
