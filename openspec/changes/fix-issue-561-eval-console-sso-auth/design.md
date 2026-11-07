## Context

`build_authorize_request(auth_enabled)` in
`src/interfaces/chat_app/evaluation_console.py` returns the callable that the
evaluation blueprint's `before_request` hook (`evaluation_routes.py:86-106`)
calls with one permission per request. The hook returns whatever the callable
returns, so a `(response, status)` pair or a `Response` object both short-circuit
the route, and `None` lets it run.

The main app's decorators (`app.py:3569` `require_auth`, `:3616` `require_perm`)
decide an anonymous request in this order: auth off → allow; logged in → check
permission; SSO on and `get_registry().allow_anonymous` false → audit and
redirect to `url_for("login")`; otherwise API request → JSON 401, browser →
redirect to `url_for("login")`. `is_api_request()` (`src/utils/rbac/decorators.py:52`)
takes no argument and reads the current Flask request.

An SSO login stores the same session keys a basic login stores
(`session["logged_in"] = True`, `session["roles"]`, `session["auth_method"]`,
`app.py:3340-3342`). So the console's existing session check already accepts an
SSO session; what differs from the main app is only the anonymous answer.

## Goals / Non-Goals

Goals: answer anonymous callers like the main app; accept an SSO session with
VIEW; keep the fail-closed default for a caller that passes no predicates; keep
the decision in the tested seam.

Non-goals: bearer tokens; any change to `can_view_evaluations`; any change to the
403 body.

## Decisions

**D1 — Three keyword-only, zero-argument predicates.**
`build_authorize_request(auth_enabled, *, sso_enabled=_never, allow_anonymous=_always,
is_api_request=_always)` where `_never` returns `False` and `_always` returns
`True`. The issue text writes `is_api_request(request)`; the real predicate takes
no argument, so the parameter is a zero-argument callable and the call site
passes `src.utils.rbac.is_api_request` itself. Predicates are called per request,
not at build time, because `allow_anonymous` is registry state the main app also
reads per request.

**D2 — The defaults reproduce today exactly.** With no predicates: SSO is off (no
audit, no SSO redirect) and every request is an API request, so every anonymous
request gets the existing JSON 401 body, byte for byte. The four existing
`test_authorize_request_*` tests stay unchanged and green.

**D3 — Anonymous answer, in the main app's order.** With auth on and no
`session["logged_in"]`:
1. `sso_enabled()` and not `allow_anonymous()` → call
   `log_authentication_event(user="anonymous", event_type="anonymous_redirect",
   success=False, method="web", details=f"path={request.path}, method={request.method}")`.
2. `is_api_request()` → JSON 401 (the existing body).
3. otherwise → `redirect(url_for("login"))`.

This departs from the main app in one place, on purpose: on SSO with
`allow_anonymous` false, the main app redirects an API request too; the console
answers it with 401. The issue's decision states "an unauthenticated API request
gets 401 (fail-closed default kept)", and every console JSON route is under
`/api/evaluations/`, where a redirect to an HTML login page gives a client
nothing to parse. The audit event still fires for that API request, so the
anonymous hit is not lost.

**D4 — Logged-in path unchanged.** Permission check, warning log, and JSON 403 body
stay as they are. The SSO session is accepted because it carries the same keys;
a test pins that with `auth_method="sso"`.

**D5 — `url_for("login")` inside the seam.** The seam builds the redirect itself,
like the main app. Tests register a stub `login` endpoint on the test Flask app
(a new `_flask_app_with_login()` helper) so `url_for` resolves; the production
app registers `login` already.

**D6 — Call site.** In `app.py` the one call becomes
`build_authorize_request(self.auth_enabled, sso_enabled=lambda: self.sso_enabled,
allow_anonymous=lambda: get_registry().allow_anonymous, is_api_request=is_api_request)`.
`get_registry` and `is_api_request` are already imported in `app.py`
(`:115`, `:118`). No other `app.py` line changes.

**D7 — Docs.** One short paragraph plus a YAML example in the
"Chat-app evaluation configuration" section of `docs/docs/configuration.md`:
GET routes need `evaluations:view`; starting a run, cancelling or continuing a
job, and retrying failed rows need `evaluations:run`
(`evaluation_routes.py:86-106`); every other write and all atom-draft routes need
`evaluations:manage`; a role with `*` has all three; with auth on, an anonymous
browser is sent to the login page and an anonymous API client gets 401.

## Risks / Trade-offs

- A redirect loop is not possible: `login` is a main-app route outside the
  evaluation blueprint, so the blueprint hook never runs on it.
- If a future caller passes predicates that raise, the request fails with 500
  instead of 401. Accepted: the only caller passes the main app's own predicates.
