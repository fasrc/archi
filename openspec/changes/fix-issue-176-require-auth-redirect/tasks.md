## 1. Red tests (TDD)

- [x] 1.1 Add `tests/unit/test_require_auth.py`. Build a minimal Flask app that registers a
  `login` endpoint (so `url_for("login")` resolves) plus one guarded `/chat`-style route and
  one guarded `/api/...` route. Instantiate `FlaskAppWrapper` via `object.__new__(FlaskAppWrapper)` and
  attach only `auth_enabled` / `sso_enabled`, following the pattern in
  `tests/unit/test_chat_refresh_context.py`.
- [x] 1.2 Assert an unauthenticated non-API request returns `302` to the `login` endpoint.
  **This is the assertion that must fail before the fix** — today it returns `401`.
- [x] 1.3 Assert an unauthenticated `/api/**` request returns `401` with the body
  `{"error": "Unauthorized", "message": "Authentication required"}` byte-for-byte.
- [x] 1.4 Assert a JSON-content-type request to a non-API guarded path returns `401`, not a
  redirect.
- [x] 1.5 Assert the SSO branch still redirects **and** still logs `anonymous_redirect`
  (patch `src.interfaces.chat_app.app.get_registry` and assert on
  `log_authentication_event`), including for an `/api/` path where SSO outranks the split.
- [x] 1.6 Assert pass-through for `auth_enabled=False` and for a logged-in session.
- [x] 1.7 Mirror 1.2/1.3 for `require_perm`: an unauthenticated browser request to a
  permission-guarded page redirects; an `/api/` one still gets the `401`.
- [x] 1.8 Add the `ast` reachability probe as a test asserting no statement follows an
  unconditional `return` inside any `decorated_function` in `app.py`.
- [x] 1.9 Run `python -m pytest tests/unit/test_require_auth.py -q` and confirm the redirect
  assertions (1.2, 1.7) FAIL for the right reason — a `401` where a `302` was expected.
  Watch it go red before touching `app.py`.

## 2. Extract the shared predicate

- [x] 2.1 Add `is_api_request()` to `src/utils/rbac/decorators.py` returning
  `request.is_json or request.path.startswith("/api/")`, with a docstring naming it the
  canonical predicate. Pure addition — do **not** rewrite the four existing open-coded sites
  (`:79`, `:130`, `:246`, `:364`); see design.md for why.
- [x] 2.2 Add a direct unit test for the predicate covering: `/api/` path, non-API path,
  JSON content type on a non-API path, and a plain browser `GET`.

## 3. Implement

- [x] 3.1 In `require_auth`, delete the unconditional `return (jsonify(...), 401)` that
  precedes the `if request.path.startswith("/api/")` block, and switch that condition to
  `is_api_request()`. Keep the `401` body unchanged and keep the comment accurate.
- [x] 3.2 In `require_perm`, wrap the unauthenticated `401` fallthrough in
  `if is_api_request():` with `return redirect(url_for("login"))` as the else. Do not touch
  the permission check or the `403` path.
- [x] 3.3 Confirm no unrelated reflow: `git diff` on `app.py` must show only these hunks.

## 4. Verify green + gate

- [x] 4.1 `python -m pytest tests/unit/test_require_auth.py -q` — all green.
- [x] 4.2 Mutation-check non-vacuity: revert 3.1 alone and confirm the 1.2 assertion fails;
  restore.
- [x] 4.3 Run the gate bare: `bash scripts/gate.sh` (never piped or redirected, never
  `--no-verify`). Confirm exit 0 and diff coverage ≥80% **on the changed lines**, not merely
  a passing total.

## 5. Ship

- [x] 5.1 Commit (short lowercase subject, no `Co-Authored-By`) and push
  `fix/issue-176-require-auth-redirect`.
- [ ] 5.2 Run `/codex:adversarial-review --wait` on the branch; address findings that hold,
  push back with reasons on those that do not; repeat until a clean round or only nits.
- [ ] 5.3 Open the PR against `fasrc/archi:dev` with `closes #176`. State in the body the two
  decisions the issue left open: `require_perm` is fixed here (it guards three browser
  pages), and the new redirect emits no audit event (the response it replaces logs nothing).
