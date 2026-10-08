## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-558-ingest-progress-counter` exists, cut from `origin/dev`
      at `5564e016`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-558-ingest-progress-counter --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D6). Follow the design; where
      the issue body and the design differ, the design wins (for example: the callback
      name is `embedding_progress`, and there are FOUR commit sites, not three).

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing test,
  run it and watch it fail, make it pass, then commit — all in one task.
- **Every task except 6.1 ends with exactly one commit** (tick its checkbox in this file
  in the same commit).
- Do not implement a later task's behaviour early. Each task's red must still fail when
  that task starts.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not already
  used. No linter runs, so a duplicate `def test_...` silently replaces the older test.
- Never append a test at the very end of an existing file without checking the diff:
  after each insert, `git diff` the file and confirm the test above yours still ends with
  its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- When a new keyword argument breaks an existing test fake, widen the fake's signature
  only (design D2 lists the sites). Do not change that test's assertions.
- No test may use `threading.Barrier`, `Event.wait()` without a timeout, or any wait
  inside a locked path. The existing fakes in `test_ingestion_status_lock.py` already show
  the safe pattern; copy it.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py`, `src/bin/service_data_manager.py`, or any
  control-plane file. Do not write under `docs/` except the edits in task 5. The PR body
  goes in `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.

## 1. Status dict: the `progress` key

- [x] 1.1 In `tests/unit/test_ingestion_status_lock.py`, add tests, then implement design D1
      in `src/utils/ingestion_status.py`, one commit
      (`feat: ingestion status carries a progress counter`). Assert:
      (a) `get_ingestion_status()` before any run has `progress` `None` and the same
      `state`/`step`/`error` as today;
      (b) `set_ingestion_progress(3, 10)` gives `{"done": 3, "total": 10}`, and
      `set_ingestion_progress(4)` gives `{"done": 4, "total": None}`;
      (c) a dict returned by `get_ingestion_status()` is not changed by a later
      `set_ingestion_progress` call;
      (d) `set_ingestion_status("running", step="x")` after `set_ingestion_progress(3, 10)`
      leaves `progress` at `{"done": 3, "total": 10}`;
      (e) a fake `run_ingestion_fn(progress_callback=None, embedding_progress=None)` that
      calls `embedding_progress(0, 2)` then `embedding_progress(2, 2)` — the status read
      inside the fake after the second call shows `{"done": 2, "total": 2}`, and the
      completed payload keeps it;
      (f) a second `run_initial_ingestion_async()` whose fake reads the status before it
      reports anything sees `progress` `None` (the reset).
      `set_ingestion_progress` is returned in the helpers dict. Widen the five existing
      `fake_run_ingestion(progress_callback=None)` fakes with `**_kwargs` (design D2).

## 2. Vectorstore manager reports committed batches

- [x] 2.1 In `tests/unit/test_vectorstore_manager_batch_commit.py`, add tests (insert them
      directly after `test_add_to_postgres_commits_every_25_files`, not at the end of the
      file), then implement design D3 and D4 in
      `src/data_manager/vectorstore/manager.py` `_add_to_postgres`, one commit
      (`feat: report embedding progress per committed batch`). Reuse the setup of
      `test_add_to_postgres_commits_every_25_files` (extract a small helper in the test
      file if you need it twice; do not change that test's assertions). Assert:
      (a) with 26 files and a recording callback, the calls are exactly
      `[(0, 26), (25, 26), (26, 26)]`;
      (b) with an `embed_documents` that raises for one file, the counter still reaches
      26 (the embed-failure commit site counts);
      (c) with a hierarchical manager (copy the setup from
      `tests/unit/test_vectorstore_manager_hierarchical.py`
      `test_add_to_postgres_hierarchical_persists_parents_and_children`), one file gives
      `[(0, 1), (1, 1)]`;
      (d) a file whose loader returns `None` (skipped by `process_file`) is never counted:
      3 files with 1 skipped gives a last call of `(2, 3)`;
      (e) a callback that raises on every call: `_add_to_postgres` returns normally, the
      commit count is unchanged, and a warning is logged;
      (f) no callback: the existing test still passes unchanged.
      Each of the four commit sites gains the running sum and one
      `_report_embedding_progress(...)` call; the `(0, total)` call goes just before the
      `for file_idx ...` loop.

## 3. Thread the callback from the status helpers to the commit sites

- [x] 3.1 Add tests, then implement design D2, one commit
      (`feat: thread embedding progress from ingestion to vectorstore`).
      In `tests/unit/test_ingest_run.py` (insert above the last test, not at the end):
      `update_vectorstore(embedding_progress=cb)` passes `cb` to `_sync_vectorstore`
      (fake with `**kwargs` that records them); `update_vectorstore()` passes `None`.
      In `tests/unit/test_vectorstore_reingest_chunk_refresh.py` (or a new test in the
      same style): `_sync_vectorstore(embedding_progress=cb)` calls the `_add_to_postgres`
      `MagicMock` with `embedding_progress=cb`.
      Create `tests/unit/test_data_manager_embedding_progress.py`: build a `DataManager`
      with `DataManager.__new__(DataManager)` and fake `localfile_manager`,
      `scraper_manager`, `ticket_manager`, `persistence` (with a `catalog` that has
      `refresh()` and `file_index = {}`) and `vector_manager` (a `MagicMock`); assert
      `run_ingestion(embedding_progress=cb)` calls
      `vector_manager.update_vectorstore(embedding_progress=cb)`, and `run_ingestion()`
      calls it with `embedding_progress=None`. If importing `src.data_manager.data_manager`
      needs stubs, copy the stub block pattern from
      `tests/unit/test_vectorstore_manager_batch_commit.py`.
      (Task 1.1 already made `run_initial_ingestion_async` pass `embedding_progress`; do
      not re-test it here.)
      Widen the two `_sync_vectorstore` lambdas in `tests/unit/test_ingest_run.py`
      (`lambda **_: ...`). Do not change the other `update_vectorstore()` callers.

## 4. Benchmark stall rule

- [ ] 4.1 In `tests/unit/test_benchmark_ingest_wait.py`, add tests (insert them above
      `test_default_fetch_parses_the_status_payload`, not at the end of the file), then
      implement design D5 in `src/bin/service_benchmark.py`, one commit
      (`feat: benchmark stall budget follows the progress counter`). Use the existing
      `FakeClock`, `_bench`, `_budget_env`, `_running` and `_scripted` helpers; add a
      `_running_with(done, total=100)` helper that returns `_running()` plus
      `"progress": {"done": done, "total": total}`. Assert:
      (a) a frozen counter: `_running_with(5)` forever with `stall="30"` raises
      `TimeoutError` with "no progress reported";
      (b) an advancing counter: `done` steps 0, 1, 2, … each poll for longer than the
      stall budget in total, then `completed` → returns a float, no error;
      (c) an advancing counter that then freezes raises a stall `TimeoutError`;
      (d) no `progress` key, `"progress": None`, `"progress": "x"`,
      `{"done": True}`, `{"done": -1}`, `{"done": "3"}` and `{}` each behave as today
      (a constant `running` payload outlives the stall budget, as in
      `test_healthy_running_ingest_outlives_the_stall_budget`);
      (e) `pending` and `running`/`initializing` payloads that carry an advancing counter
      still trip the stall budget;
      (f) the pure helpers directly: `_ingest_progress_done` on each payload shape above,
      and `_ingest_is_progressing` for rules 1–4 of D5;
      (g) the observed-ingest return value still starts at the first accepted poll.
      Add `progress=%s` to the poll log line. Update the docstrings and the
      `BENCH_INGEST_MAX_WAIT=0` comment per design D6; keep the warning's message text.

## 5. Docs

- [ ] 5.1 Edit `docs/docs/benchmarking.md` (the `BENCH_INGEST_WAIT_TIMEOUT` table row and
      the "The ingest is alive but stuck" bullet) and `docs/docs/api_reference.md`
      (`GET /api/ingestion/status`: the four keys, the `progress` shape, that `done` counts
      committed files and can end below `total`, that `progress` is `null` outside the
      embedding loop and is reset per ingest), per design D6. Say that a batch is 25
      files and that an operator with very slow batches can raise
      `BENCH_INGEST_WAIT_TIMEOUT`. If `mkdocs` is installed, run
      `mkdocs build --strict -f docs/mkdocs.yml` and read its INFO lines for a broken
      anchor; if it is not installed, say so in the commit body. One commit
      (`docs: ingestion progress counter and stall rule`).

## 6. Publish

- [ ] 6.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat -- docs/` lists only `docs/docs/benchmarking.md` and
      `docs/docs/api_reference.md`. Push with
      `git push -u origin fix/issue-558-ingest-progress-counter`. Write the PR body to
      `/tmp/pr-558.md` with `Closes #558` in the body (never the title), a per-task
      summary, and a "Post-merge operator check" line: after a redeploy, poll
      `curl -s http://localhost:7871/api/ingestion/status` during a real ingest and
      confirm `progress.done` advances. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-558-ingest-progress-counter --title "ingestion: progress counter in the status payload, keyed into the benchmark stall budget" --body-file /tmp/pr-558.md`.
      This task changes no tracked file; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-558-ingest-progress-counter` prints the PR.
