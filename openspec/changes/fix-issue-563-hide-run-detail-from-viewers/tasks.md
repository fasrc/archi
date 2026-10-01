Work order: issue #563 (milestone v2026.11.0, tier `sonnet`). Branch
`fix/issue-563-hide-run-detail-from-viewers`, cut from `origin/dev` `26e6429e`. Read
`proposal.md` and `design.md` in this directory first. Every task ends with a green gate
and a commit — no task ends red.

## 1. The redaction seam: red, green, gate, commit

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Create `tests/unit/evaluation/qa/test_run_visibility.py` with the
      module tests of design D4 (the full-payload test, the edge-case test, the literal
      field-list test, the console-service test, and the resolved-config source test).
      Import `from src.evaluation.qa.run_visibility import (VIEWER_HIDDEN_FIELDS,
      VIEWER_HIDDEN_JUDGMENT_FIELDS, redact_run_for_viewer)`. Run
      `python -m pytest tests/unit/evaluation/qa/test_run_visibility.py -q --no-header`
      and confirm it fails with `ModuleNotFoundError` for `run_visibility` (not a fixture
      or syntax error).

      **Then green.** Create `src/evaluation/qa/run_visibility.py` as in design D1–D2: a
      module docstring that names issue #563 and says why `answer_sha256` and judgment
      `rationale` are hidden, the two constants, and `redact_run_for_viewer`. In
      `src/evaluation/qa/console.py`, change `get_run` to
      `get_run(self, history_id: str, *, include_hidden: bool = False)` as in design D3.
      Change nothing else in `console.py`.

      Then run the new test file (all pass), `black` and `isort` the changed files,
      `git add` them, `bash scripts/gate.sh` exits 0, commit
      `fix(#563): add a viewer redaction seam for evaluation run detail`.

## 2. Wire the route

- [ ] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** In `tests/unit/test_evaluation_routes.py`, change `_Service.get_run`
      and the lambda at line 669 as design D4 says, then append the route tests of design
      D4 after the last existing test (check that the last test keeps all of its
      assertions). Confirm that the new MANAGE and auth-off tests fail on their assertions:
      the route does not yet pass `include_hidden`, so the fake receives the default
      `False`. The VIEW-only test can pass already; that is expected.

      **Then green.** In `src/interfaces/chat_app/evaluation_routes.py` `run_detail`, pass
      `include_hidden=_can_manage()` to `_service().get_run`. Change nothing else in that
      file. `black --check src/interfaces/chat_app/evaluation_routes.py` must pass.

      Then run `python -m pytest tests/unit/test_evaluation_routes.py
      tests/unit/test_evaluation_console.py tests/unit/evaluation/qa -q --no-header` (all
      pass), confirm `git diff origin/dev -- tests/unit | grep -c '^-.*def test_'` prints
      `0`, format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#563): hide the run-detail answer key from view-only callers`.

## 3. Publish

- [ ] 3.1 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-563-hide-run-detail-from-viewers`. Confirm the push
      landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-563-hide-run-detail-from-viewers` must print
      the same SHA as `git rev-parse HEAD`. If it prints nothing, stop and write the halt
      reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-563.md` — **never** under `docs/` — and include
      the literal line `Closes #563`, the hidden-field table from design D1, the anchor
      correction from `proposal.md` (the resolved config was never in the payload), and
      the test counts of the two test files. If no PR exists for the branch yet
      (`gh pr list --repo fasrc/archi --head fix/issue-563-hide-run-detail-from-viewers`
      is empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#563): hide the run-detail answer key from view-only callers" --body-file /tmp/pr-body-563.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-563.md`
      instead. Verify the link:
      `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences` must list 563. If
      it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
python -m pytest tests/unit/evaluation/qa/test_run_visibility.py -q --no-header
python -m pytest tests/unit/test_evaluation_routes.py tests/unit/test_evaluation_console.py -q --no-header

# no test was deleted — must print 0
git diff origin/dev -- tests/unit | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
