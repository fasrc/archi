## Context

`FlaskAppWrapper.require_auth`'s unauthenticated path on `origin/dev` @ `904c64d7`:

```python
if not session.get("logged_in"):
    if self.sso_enabled:                                   # :3448
        registry = get_registry()
        if not registry.allow_anonymous:
            log_authentication_event(... "anonymous_redirect" ...)
            return redirect(url_for("login"))              # :3460  reachable, correct

    # Return 401 Unauthorized response for API requests
    return (jsonify({"error": "Unauthorized",
                     "message": "Authentication required"}), 401)   # :3463-3468  UNCONDITIONAL
    if request.path.startswith("/api/"):                   # :3469  DEAD
        return (jsonify({...}), 401)
    else:
        return redirect(url_for("login"))                  # :3480  DEAD
```

Reachability, by state:

| `auth_enabled` | `logged_in` | `sso_enabled` | `allow_anonymous` | Today | After |
|---|---|---|---|---|---|
| false | — | — | — | passes through | unchanged |
| true | true | — | — | passes through | unchanged |
| true | false | true | false | `302` + audit event (`:3460`) | unchanged |
| true | false | true | true | `401` | `401` for API/JSON, `302` for browser |
| true | false | false | — | `401` | `401` for API/JSON, `302` for browser |

`require_perm` reaches the same `401` from `:3512-3520` with no dead code — it is simply
written as an unconditional fallthrough.

## Goals / Non-Goals

**Goals:**
- Remove the unreachable statement; the `ast` probe must report nothing inside
  `decorated_function`.
- Browser navigations to guarded non-API routes land on the login page.
- `/api/**` responses — status *and* body bytes — are untouched.
- The SSO `anonymous_redirect` branch and its audit event survive, proven by test.
- First test coverage for both decorators.

**Non-Goals:**
- No change to permission evaluation, the `403` Forbidden response, or the SSO callback.
- No migration of the four existing open-coded predicate sites in
  `src/utils/rbac/decorators.py` (see the last decision).
- No new auth/session behaviour — only the *form* of an existing rejection changes.

## Decisions

**Decision: share the predicate, not the response.**
`src/utils/rbac/decorators.py` already implements this split four times, but its JSON body
is `{"error": "Authentication required", "message": "Please log in to access this
resource", "status": 401}` — different from `app.py`'s `{"error": "Unauthorized",
"message": "Authentication required"}`. Extracting a shared *response* helper would force
one of the two bodies to change; the issue's acceptance criteria require `app.py`'s to stay
byte-identical because existing callers depend on it. So the extracted unit is the
predicate `is_api_request()` and each site keeps its own body.
- *Alternative considered:* a shared `unauthenticated_response()` returning the full tuple —
  rejected, it silently changes the API contract for every `/api/**` consumer.

**Decision: adopt the canonical predicate `request.is_json or request.path.startswith("/api/")`.**
The dead branch tested only the path prefix. The rbac module's four sites also treat a JSON
content type as an API request, and that is the safer superset: a JSON client posting to a
non-`/api/` guarded path keeps a machine-readable `401` instead of a `302` to an HTML login
page it cannot render. Standardising on the predicate already used four times in the tree
beats reviving a fifth, weaker variant.
- *Alternative considered:* preserve `path.startswith("/api/")` exactly as the dead code
  wrote it — rejected: it is strictly worse and would make the new helper disagree with the
  four callers it is meant to unify.
- *Note:* under `require_auth` the two predicates are indistinguishable today — its only
  non-API routes are the `GET` pages `/chat` and `/terms`. The difference is a guard against
  future non-API JSON routes, not a present-day behaviour change.

**Decision: fix `require_perm` in the same change, and say so loudly.**
The issue permits either answer. The route table decides it: `require_perm` guards `/data`,
`/upload`, and `/admin/database` — three HTML pages a human navigates to. Leaving them
returning `401` would fix one instance of the defect and knowingly leave three. This is not
a silent drive-by: it is specified here, covered by its own tests, and called out in the PR.
- *Alternative considered:* extract one decorator that the other delegates to — rejected as
  too large for a bug fix; the two differ in audit logging and in the extra permission step,
  and merging them would put an untested refactor under a `401`/`403` security boundary.

**Decision: the new browser redirect emits no audit event.**
The SSO branch logs `anonymous_redirect` because enforced-SSO bouncing an anonymous user is
security-relevant. The new redirect replaces a response that logs *nothing* today, and it
fires on ordinary anonymous page views of `/chat` — including from crawlers and health
checks. Changing the *form* of a rejection should not change its audit semantics, and
logging every anonymous page view would dilute the auth log. The SSO event is untouched, and
a test pins that it still fires.
- *Revisit if:* the login-redirect rate ever needs monitoring; then add it deliberately with
  its own sampling, not as a side effect of this fix.

**Decision: add `is_api_request()` as a pure addition; do not migrate the four rbac sites.**
Those four sites are already correct — the duplication is cosmetic, not a defect. Rewriting
them turns currently-uncovered lines (`decorators.py:79-80, 92, 145, 249-250, 261, 381`)
into *modified* lines that `diff-cover --fail-under=80` would charge this PR for, mixing an
untested refactor into a security fix. This change introduces the shared predicate and uses
it at the two broken sites; migrating the four correct ones is a follow-up.

## Risks / Trade-offs

- **[An API consumer relied on `/chat` returning 401]** → `/chat` and `/terms` render HTML
  for humans; a programmatic client would be reading a login page's markup either way. Any
  client sending a JSON content type still gets the `401` via the `is_json` half of the
  predicate.
- **[Redirect loop if `/login` were itself guarded]** → `/login` is registered at `:3169`
  with no decorator, so it cannot bounce. A test asserts the redirect target resolves to the
  `login` endpoint rather than asserting a hardcoded path.
- **[Diff coverage on an 18%-covered file]** → the changed lines are exactly the lines the
  new tests drive; `require_auth`'s deletion is unchanged-context for diff-cover and is
  covered anyway.
- **[`request.is_json` semantics]** → it reflects the `Content-Type` header, so a plain
  browser `GET` is never JSON. Pinned by test rather than trusted from memory.

## Migration Plan

1. Land code + tests through `bash scripts/gate.sh` on
   `fix/issue-176-require-auth-redirect`; PR to `fasrc/archi:dev` with `closes #176`.
2. No data, config, or schema migration. No redeploy required for correctness, though the
   behaviour is only observable on a deployment with `auth_enabled` true.
3. **Rollback:** revert the commit. The dead code returns and browsers get `401` again.

## Open Questions

- None blocking. The two deliberately-made calls the issue left open — `require_perm`
  scope and the audit event — are decided above and restated in the PR body.
