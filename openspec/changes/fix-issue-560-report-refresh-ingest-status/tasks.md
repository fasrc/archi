## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-560-report-refresh-ingest-status` exists, cut from
      `origin/dev` at `0189e2dd`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-560-report-refresh-ingest-status --strict` passed on
      the host. The `openspec` CLI is **not usable in this container** — do not run it, and
      do not add a task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D5). Follow the design; where
      the issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing test,
  run it and watch it fail, make it pass, then commit — all in one task.
- **Every task except 4.1 ends with exactly one commit** (tick its checkbox in this file
  in the same commit).
- Do not implement a later task's behaviour early. Each task's red must still fail when
  that task starts.
- Before you add a test, `grep -n 'def test_' tests/unit/test_ingestion_status_lock.py`
  and pick a name that is not already used. No linter runs, so a duplicate `def test_...`
  silently replaces the older test.
- Add new tests at the END of `tests/unit/test_ingestion_status_lock.py`. After each
  insert, `git diff` the file and confirm that the test above yours
  (`test_second_run_initial_ingestion_resets_progress`) still ends with its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- No test may use `threading.Barrier`, `Event.wait()` without a timeout, or any wait
  inside a locked path. Every `join` and `wait` has `timeout=5` or less.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py` or any control-plane file. Do not write
  under `docs/` except the edit in task 3. The PR body goes in `/tmp`, never in the repo.
  Do not add an entry to `docs/questions.md`.

## 1. `run_tracked` in the status helpers

- [x] 1.1 In `tests/unit/test_ingestion_status_lock.py`, add tests, then implement design
      D1 in `src/utils/ingestion_status.py`, one commit
      (`feat: run_tracked publishes a refresh lifecycle`). Assert:
      (a) inside `fn`, `get_ingestion_status()` has `state == "running"`,
      `step == "upload"`, `error is None`, `progress is None`; after the call,
      `state == "completed"` and `step == "done"`; the return value of `fn` is returned;
      (b) after `set_ingestion_progress(3, 10)`, a `run_tracked` call shows
      `progress is None` inside `fn` (the reset, design D3);
      (c) an `fn` that raises `RuntimeError("boom")`: `pytest.raises(RuntimeError)`, then
      the status has `state == "error"`, `step == "failed"`, `error == "boom"`;
      (d) the lock order: the test thread acquires the `RLock` it passed to
      `build_ingestion_helpers` and calls `set_ingestion_status("running",
      step="embedding")`; a second thread calls `run_tracked("upload", fn)`; after
      `time.sleep(0.2)` the status still has `step == "embedding"` and `fn` has not run;
      the test releases the lock, joins the thread with `timeout=5`, and the status is
      `completed`. Release the lock in a `finally`;
      (e) re-entrancy: `run_tracked` called while the same thread already holds the lock
      completes (no deadlock; join with `timeout=5` if you use a thread).
      Return `run_tracked` in the helpers dict and add it to the docstring list.

## 2. `run_source_refresh` and the call sites

- [ ] 2.1 Add tests, then implement design D2 in `src/utils/ingestion_status.py` and
      design D4 in `src/bin/service_data_manager.py`, one commit
      (`feat: report scheduled and upload refreshes as running`). Assert, with a recording
      `set_source_status` fake and a `update_vectorstore` fake (`**kwargs` recorder):
      (a) `run_source_refresh("git", func, update_vectorstore, set_source_status)`:
      inside `func` the status has `state == "running"` and `step == "scheduled:git"`;
      after the call `state == "completed"`;
      (b) the call order is exactly: `set_source_status("git", state="running")`, `func`,
      `update_vectorstore(force=True)`, `set_source_status("git", state="idle",
      last_run=<str>)`, and `datetime.fromisoformat(last_run)` has a UTC offset of 0;
      (c) an `update_vectorstore` that raises `RuntimeError("embed down")`: the call
      raises, the status is `error` with `error == "embed down"`, and the idle
      `set_source_status` call never happened.
      Then do D4 steps 1–4 in `service_data_manager.py`. Keep the `def run_locked` and
      `def trigger_update` lines unchanged. Check the added-line count:
      `git diff -U0 origin/dev -- src/bin/service_data_manager.py | grep -c '^+[^+]'` must
      print 7 or less. Run `python -c "import ast,sys;
      ast.parse(open('src/bin/service_data_manager.py').read())"` to confirm the file
      parses (no unit test imports it).

## 3. Docs

- [ ] 3.1 Edit `docs/docs/api_reference.md`, `GET /api/ingestion/status` (near line 686),
      per design D5: the `step` values `scheduled:<source>` and `upload` while `running`,
      `completed`/`done` or `error`/`failed` after, that `progress` is `null` during these
      runs, and that a benchmark that waits on this endpoint also waits for them. If
      `mkdocs` is installed, run `mkdocs build --strict -f docs/mkdocs.yml` and read its
      INFO lines for a broken anchor; if it is not installed, say so in the commit body.
      One commit (`docs: refresh and upload runs in the ingestion status`).

## 4. Publish

- [ ] 4.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat -- docs/` lists only `docs/docs/api_reference.md`.
      Push with `git push -u origin fix/issue-560-report-refresh-ingest-status`. Write the
      PR body to `/tmp/pr-560.md` with `Closes #560` in the body (never the title), a
      per-task summary, and a "Post-merge operator check" line: after a redeploy, upload a
      file in the uploader and poll
      `curl -s http://localhost:7871/api/ingestion/status`; confirm `state` is `running`
      with `step` `upload`, then `completed`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-560-report-refresh-ingest-status --title "ingestion: report scheduled and upload refreshes as running" --body-file /tmp/pr-560.md`.
      This task changes no tracked file; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-560-report-refresh-ingest-status` prints
      the PR.
