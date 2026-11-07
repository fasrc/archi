## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-650-source-status-error` exists, cut from `origin/dev` at
      `0ed50a23`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-650-source-status-error --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D5), including the UI check
      (D5: no UI change). Follow the design; where the issue body and the design differ,
      the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing tests,
  run them and watch them fail, make them pass, then commit — all in one task.
- Task 1.1 ends with exactly one commit (tick its checkbox in this file in the same commit).
- Before you add a test, `grep -n 'def test_' tests/unit/test_ingestion_status_lock.py`
  and pick a name that is not already used. No linter runs, so a duplicate `def test_...`
  silently replaces the older test.
- Add new tests at the END of `tests/unit/test_ingestion_status_lock.py`. After the
  insert, `git diff` the file and confirm that the last existing test
  (`test_a_run_joining_during_lock_release_clears_completed`) still ends with its own
  `assert helpers["get_ingestion_status"]()["state"] == "completed"`.
- Never delete or edit an existing test. After the commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- No test may use `threading.Barrier` or an unbounded wait. Every `join` and `wait` has
  `timeout=5` or less.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After the
  commit, `git status --porcelain` must be empty.
- Never bypass the commit hook. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py`, `src/bin/service_data_manager.py`, any UI
  file, or any control-plane file. Do not write under `docs/`. The PR body goes in `/tmp`,
  never in the repo. Do not add an entry to `docs/questions.md`.

## 1. `run_source_refresh` writes a terminal error status

- [x] 1.1 In `tests/unit/test_ingestion_status_lock.py`, add tests, then implement design
      D1–D4 in `src/utils/ingestion_status.py` (`run_source_refresh` only), one commit
      (`fix: a failed scheduled refresh leaves its source at error`). Use a recording
      `set_source_status(source, **kwargs)` fake and the
      `build_ingestion_helpers(lambda **_: None, threading.RLock())` setup of the existing
      `run_source_refresh` tests. Assert:
      (a) `func` raises `RuntimeError("collect down")`: `pytest.raises(RuntimeError,
      match="collect down")`; the recorded calls are exactly
      `[("git", {"state": "running"}), ("git", {"state": "error"})]`; the
      `update_vectorstore` fake was not called; `get_ingestion_status()` has
      `state == "error"` and `error == "collect down"`;
      (b) `update_vectorstore` returns `"failed"`: `pytest.raises(RuntimeError)`; the last
      recorded call is `("git", {"state": "error"})`; no recorded call has
      `state == "idle"`; no recorded call has a `last_run` key;
      (c) `func` raises `RuntimeError("collect down")` and the fake raises
      `OSError("disk full")` when called with `state="error"`:
      `pytest.raises(RuntimeError, match="collect down")` (the `OSError` must not escape);
      (d) the success path is unchanged: run the existing
      `test_run_source_refresh_call_order` and confirm it still passes.
      Watch (a), (b) and (c) fail on the unmodified code before you change
      `src/utils/ingestion_status.py`.

## 2. Publish

- [ ] 2.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat` lists only `src/utils/ingestion_status.py`,
      `tests/unit/test_ingestion_status_lock.py`, and files under
      `openspec/changes/fix-issue-650-source-status-error/`. Push with
      `git push -u origin fix/issue-650-source-status-error`. Write the PR body to
      `/tmp/pr-650.md` with `Closes #650` in the body (never the title), a per-task
      summary, and a "Post-merge operator check" line: after a redeploy, a scheduled
      refresh whose collector fails shows `"state": "error"` for that job in
      `curl -s http://localhost:7871/api/schedules`, not `running`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-650-source-status-error --title "ingestion: a failed scheduled refresh leaves its source at error" --body-file /tmp/pr-650.md`.
      This task changes only this checkbox; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-650-source-status-error` prints the PR.
