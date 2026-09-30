## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-561-eval-console-sso-auth` exists, cut from `origin/dev` at
      `e58a7ada`, and is pushed to `origin` with upstream set. You are on it. Do not
      re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-561-eval-console-sso-auth --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does.
- [x] 0.3 Baseline measured at `e58a7ada`:
      `python -m pytest tests/unit/test_evaluation_console.py tests/unit/test_evaluation_routes.py -q --no-header`
      → **67 passed**. The red: with `build_authorize_request(True)`, an anonymous browser
      `GET /evaluations` gets `401`, not a redirect.
- [x] 0.4 Every design decision is made in `design.md` (D1–D7). Where the issue body and the
      design differ (the issue writes `is_api_request(request)`; the real predicate takes no
      argument), the design wins.

## Rules that apply to EVERY task below

- Code work is in `src/interfaces/chat_app/evaluation_console.py`,
  `tests/unit/test_evaluation_console.py`, and ONE call in `src/interfaces/chat_app/app.py`
  (task 2). Do not touch `evaluation_routes.py`, `can_view_evaluations`, or any template.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends red can never be committed and the loop halts there.
- Before you add a test, `grep -n 'def test_' tests/unit/test_evaluation_console.py` and pick
  a name that is not already used. The gate runs no linter, so a duplicate `def test_...`
  silently replaces the older test while the suite stays green.
- Put the new tests directly **after** `test_authorize_request_allows_a_permitted_session`
  and **before** the `@pytest.mark.parametrize` decorator of
  `test_navigation_visibility_matches_route_access`. Never append at the end of the file.
  After you insert, `git diff` the file and read the context lines: the test above yours must
  still end with its own line
  `assert authorize_request(Permission.Evaluations.MANAGE) is None`.
- Do not change the four existing `test_authorize_request_*` tests. They pin design D2.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/unit/test_evaluation_console.py | grep -c '^-.*def test_'`
  must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each commit,
  `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/` except the one edit in task 2. The PR body goes in
  `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.
- Do not add a bearer-token path. `grep -in bearer src/interfaces/chat_app/evaluation_console.py`
  must print nothing when you finish (task 2 removes the docstring mentions).

## 1. The seam: inject the three predicates and answer anonymous callers like the main app

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a helper `_flask_app_with_login()` next to `_flask_app()` that returns
      `_flask_app()` with a stub route registered at `/login` under endpoint name `login`
      (design D5). Then add these tests (use `_flask_app_with_login()` in each):
      (a) `test_authorize_request_accepts_an_sso_session_with_view` — auth on, all three
      predicates supplied (`sso_enabled=lambda: True`, `allow_anonymous=lambda: False`,
      `is_api_request=lambda: True`), session `logged_in=True`, `auth_method="sso"`,
      `roles=["viewer"]`, `has_permission` patched to `True` → `authorize_request(Permission.Evaluations.VIEW) is None`
      on `/api/evaluations/catalog`;
      (b) `test_authorize_request_redirects_an_anonymous_browser_to_login` — auth on,
      `is_api_request=lambda: False`, no session, path `/evaluations` → the result is a
      `werkzeug` `Response` (not a tuple) with `status_code == 302` and
      `location` ending in `/login`;
      (c) `test_authorize_request_answers_an_anonymous_api_client_with_401` — parametrize over
      `sso_enabled` in (`True`, `False`) with `allow_anonymous=lambda: False` and
      `is_api_request=lambda: True` → `(response, 401)` with `response.get_json()["error"] == "Unauthorized"`;
      (d) `test_authorize_request_audits_an_anonymous_sso_request` — patch
      `evaluation_console.log_authentication_event`; parametrize the three cases
      (sso on, allow_anonymous false → called once with `user="anonymous"`,
      `event_type="anonymous_redirect"`, `success=False`, `method="web"`, and `details`
      containing the path and `GET`), (sso on, allow_anonymous true → not called),
      (sso off → not called);
      (e) `test_authorize_request_without_predicates_never_redirects` — `build_authorize_request(True)`
      with no predicates, anonymous request on `/evaluations` (a browser path) → still
      `(response, 401)`, and a patched `log_authentication_event` is not called;
      (f) `test_authorize_request_rejects_an_sso_session_without_view_with_403` — predicates
      supplied, SSO session, `has_permission` patched to `False` → `(response, 403)` naming
      `Permission.Evaluations.VIEW` as `required_permission`.
      Run `python -m pytest tests/unit/test_evaluation_console.py -q --no-header` and confirm
      (a)–(d) and (f) FAIL with `TypeError` (unexpected keyword argument), and (e) fails
      with `AttributeError` because the module has no `log_authentication_event` to patch
      yet — none of them on a fixture error. Note the fail count.

      **Then green.** In `evaluation_console.py`: import `redirect` and `url_for` from `flask`,
      and `log_authentication_event` from `src.utils.rbac.audit` (a module-level name, so the
      tests can patch `evaluation_console.log_authentication_event`). Change the signature to
      `build_authorize_request(auth_enabled: bool, *, sso_enabled: Callable[[], bool] = ...,
      allow_anonymous: Callable[[], bool] = ..., is_api_request: Callable[[], bool] = ...)`
      with defaults that return `False`, `True`, `True` (design D1, D2). Implement the
      anonymous branch in the order of design D3. Leave the logged-in branch as it is (D4).
      Update the function docstring: describe the three predicates, the D3 order, the
      one deliberate departure (API → 401 even on SSO without anonymous access), and that no
      bearer path exists. Do not use the word "bearer" in the docstring; write
      "token-based authentication" instead.

      Then: all 67 baseline tests plus the new ones pass; format; `git add`;
      `bash scripts/gate.sh` exits 0; commit
      `fix(#561): answer anonymous evaluation-console callers like the main app`.

## 2. Wire the call site, fix the module docstring, document the permissions

- [x] 2.1 One commit.
      In `src/interfaces/chat_app/app.py`, change ONLY the `build_authorize_request(self.auth_enabled)`
      call inside `register_evaluations(...)` to the form in design D6. `get_registry` and
      `is_api_request` are already imported — `grep -n "get_registry,\|is_api_request," src/interfaces/chat_app/app.py`
      to confirm; add no import. `git diff src/interfaces/chat_app/app.py` must show only that
      call.
      In `evaluation_console.py`, rewrite the module docstring's last paragraph (the one that
      starts "And the ``authorize_request`` callable is narrower than upstream's") to say:
      the callable answers anonymous callers like the main app (browser → login redirect,
      API → 401, SSO without anonymous access → audited) and has no token-based
      authentication path, which is a recorded divergence from upstream. Change the "Three
      decisions" count if it no longer matches. Do not use the word "bearer".
      In `docs/docs/configuration.md`, in the "Chat-app evaluation configuration" section,
      add the paragraph and YAML example that design D7 describes, after the existing bullet
      list of that section and before the next `####` heading. Use the `auth_roles` shape the
      "Option 2: role-based via RBAC" example above it uses. Add no heading and no anchor.
      Then: `grep -in bearer src/interfaces/chat_app/evaluation_console.py` prints nothing;
      the test count is unchanged from task 1; format; `git add`; gate green; commit
      `docs(#561): wire the console auth predicates and document the evaluation permissions`.

## 3. Publish

- [x] 3.1 Push the branch and open the PR. Push with
      `git push -u origin fix/issue-561-eval-console-sso-auth`. Confirm the push landed on
      **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-561-eval-console-sso-auth` must print the same
      SHA as `git rev-parse HEAD`. If it prints nothing or a different SHA, stop and write the
      halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-561.md` — **never** under `docs/` — and include the
      literal line `Closes #561`, the measured red (anonymous browser `GET /evaluations` → 401
      at `e58a7ada`), the test count before (67) and after, and the D3 decision order in four
      lines, including the one departure from the main app. If no PR exists for the branch yet
      (`gh pr list --repo fasrc/archi --head fix/issue-561-eval-console-sso-auth` is empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#561): answer anonymous evaluation-console callers like the main app" --body-file /tmp/pr-body-561.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-561.md`
      instead. Verify the link:
      `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences` must list 561. If it
      does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the two console suites — 67 passed at e58a7ada
python -m pytest tests/unit/test_evaluation_console.py tests/unit/test_evaluation_routes.py -q --no-header

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/test_evaluation_console.py | grep -c '^-.*def test_'

# no token path — must print nothing
grep -in bearer src/interfaces/chat_app/evaluation_console.py

# the gate, before every commit
bash scripts/gate.sh
```
