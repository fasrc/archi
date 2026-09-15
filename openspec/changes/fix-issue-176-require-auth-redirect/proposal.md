## Why

`FlaskAppWrapper.require_auth` (`src/interfaces/chat_app/app.py:3433`) returns a `401` JSON body
**unconditionally** at `:3463-3468`, which makes the API-vs-browser split immediately below
it (`:3469-3480`) dead code. The comment at `:3462` ("for API requests") states the intent
the dead branch was written to implement. An `ast` reachability probe confirms it
mechanically: `unreachable after return at line 3463 -> 3469`.

The consequence is user-visible. `require_auth` guards two routes a human opens in a
browser — `/chat` (`:2784`) and `/terms` (`:2797`). With `auth_enabled` true and the SSO
redirect not taken, an unauthenticated visitor to `/chat` is served the raw JSON
`{"error": "Unauthorized", "message": "Authentication required"}` instead of the login page.

`FlaskAppWrapper.require_perm` (`:3486`) has no *unreachable* code, but it has the same defect: its
unauthenticated fallthrough at `:3512-3520` answers `401` for every path it guards, and it
guards three browser pages — `/data` (`:2987`), `/upload` (`:3045`), and `/admin/database`
(`:3141`). The issue left this call deliberately open; the route table decides it.

No test in the repo references either decorator (`grep -rn "require_auth" tests/` is empty)
and `app.py` measures ~18% line coverage, so nothing detected this.

## What Changes

- Delete the unconditional `return` in `require_auth`, bringing the existing API/browser
  split to life: API callers keep the `401`, browsers get `redirect(url_for("login"))`.
- Give `require_perm`'s unauthenticated fallthrough the same split.
- Extract the API-request predicate as `is_api_request()` in `src/utils/rbac/decorators.py`,
  which already open-codes it **four times** (`:79`, `:130`, `:246`, `:364`). Both `FlaskAppWrapper`
  decorators call the shared predicate rather than a fifth copy.
- Bring the first test coverage to both decorators.

The `401` JSON body is **unchanged**. The shared abstraction is deliberately the predicate
and not the whole response, because the two subsystems return different bodies and API
callers depend on `app.py`'s.

## Capabilities

### New Capabilities
- `chat-auth-response-negotiation`: the chat app's authentication and permission decorators
  answer an unauthenticated request in the form its caller can act on — a `401` JSON body
  for API/JSON callers, a redirect to the login page for browser navigations — while
  preserving the existing SSO anonymous-redirect behaviour and its audit event.

### Modified Capabilities
<!-- None. No existing capability in openspec/specs/ governs chat-app auth response shape. -->

## Impact

- **Code:** `src/interfaces/chat_app/app.py` — `require_auth` (dead-code deletion) and
  `require_perm` (new split). `src/utils/rbac/decorators.py` — new `is_api_request()` helper
  (pure addition; the four existing open-coded call sites are left alone, see design).
- **Tests:** new `tests/unit/test_require_auth.py`. Both decorators are currently at 2/36
  measurable lines covered, so the tests are the first exercise this code has ever had.
- **Behaviour change (intended):** an unauthenticated browser GET of `/chat`, `/terms`,
  `/data`, `/upload`, or `/admin/database` now returns `302 → /login` instead of `401`.
  Anything under `/api/**`, and any request with a JSON content type, is unaffected.
- **No** change to config schema, CLI, providers, the SSO callback, or permission
  evaluation (`has_permission`), and no change to the `403` Forbidden path.
