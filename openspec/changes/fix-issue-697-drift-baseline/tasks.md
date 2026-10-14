## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-697-drift-baseline` exists, cut from `origin/dev` at
      `c2e885ca`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-697-drift-baseline --strict` passed on the host. The
      `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D5). Follow the design; where the
      issue body and the design differ, the design wins.
- [x] 0.4 The partial-`updated`-run case is out of scope and filed as #703. Do not work it.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing tests,
  run them and watch them fail, make them pass, then commit — all in one task.
- Each of tasks 1.1, 1.2 and 2.1 ends with exactly one commit (tick its checkbox in this
  file in the same commit).
- Before you add a test, `grep -n 'def test_' <test file>` and pick a name that is not
  already used. No linter runs, so a duplicate `def test_...` silently replaces the older
  test.
- Add new tests at the END of the test file. After the insert, `git diff` the file and
  confirm that the test that was last before your insert still ends with its own last line:
  in `tests/unit/test_status_provenance.py` that is
  `assert build_deployment_panel(None)["pin_mismatch"] is False`; in
  `tests/unit/test_status_template_render.py` it is `assert "not the pinned commit" in html`.
- Do not delete an existing test. Edit an existing test only where task 1.2 says so (two
  assertion strings in `tests/unit/test_status_template_render.py`). After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never bypass the commit hook. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py`, `src/data_manager/**`, `src/utils/**`, or any
  control-plane file. Under `docs/`, edit only `docs/docs/services.md` (task 2.1). The PR
  body goes in `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.

## 1. Corpus run for config and drift

- [ ] 1.1 Implement design D2–D3 in `src/interfaces/chat_app/status_provenance.py`, test
      first, one commit (`fix: compare drift against the run that embedded the corpus`).
      Add these tests at the end of `tests/unit/test_status_provenance.py`, reusing the
      file's `_run_row`, `_deploy_row`, `_FakeConn` and `_NthCallRaisingConn` helpers:
      (a) the issue's repro on the panel: `corpus = _run_row(status="updated",
      completed_at=STARTED, config_snapshot={"categorization": False, "chunk_size": 1000})`,
      `run = _run_row(status="up_to_date", config_snapshot={"categorization": True,
      "chunk_size": 500})`, `current = {"categorization": True, "chunk_size": 500}`;
      `build_knowledge_base_panel(run, current, corpus_run=corpus)` has `config ==
      {"categorization": False, "chunk_size": 1000}`, a `drift` list that contains exactly
      the entries `{"key": "categorization", "current": True, "at_ingest": False}` and
      `{"key": "chunk_size", "current": 500, "at_ingest": 1000}` (compare as a list sorted
      by `key`), `corpus_config_available is True`, `corpus_built_at == STARTED`, and
      `status == "up_to_date"`, `completed_at == FINISHED` (last ingest unchanged);
      (b) an `updated` run ignores `corpus_run`: `run = _run_row()` and a different
      `corpus_run` snapshot; the panel's `config` is `run`'s snapshot and `corpus_built_at
      == FINISHED`;
      (c) `up_to_date` run with `corpus_run=None`: `config == {}`, `drift == []`,
      `corpus_config_available is False`, `corpus_built_at is None`, and
      `current_config_available is True` when `current_snapshot` is `{}`;
      (d) `build_knowledge_base_panel(None, {})` has `corpus_config_available is False` and
      `corpus_built_at is None`;
      (e) loader with an `up_to_date` newest run: build
      `upd = _run_row(status="updated", completed_at=STARTED, config_snapshot={"categorization":
      False, "chunk_size": 1000})` and `utd = _run_row(status="up_to_date",
      config_snapshot={"categorization": False, "chunk_size": 500})` (its `completed_at` stays
      `FINISHED`). Use `_FakeConn(results=[tuple(_deploy_row().values()),
      tuple(utd.values()), ({"chunk_size": 500},), None, tuple(upd.values())])`. The third
      result is the static-config row: its single column is the UNWRAPPED data-manager
      mapping `{"chunk_size": 500}`, NOT `{"data_manager": {...}}` —
      `build_ingest_config_snapshot` reads `chunk_size` from the top level. The fourth result
      (`None`) is the failed-attempt read; `_FakeCursor.execute` pops it, so the row is `None`.
      The fifth is the corpus read. Assert on `view["knowledge_base"]`: `config ==
      {"categorization": False, "chunk_size": 1000}`, `drift == [{"key": "chunk_size",
      "current": 500, "at_ingest": 1000}]`, `corpus_built_at == STARTED`, `status ==
      "up_to_date"`, `completed_at == FINISHED`, and `corpus_config_available is True`;
      (f) loader with an `updated` newest run executes exactly four reads: count the
      fake cursor's `execute` calls (add a counter in a NEW fake class at the end of the
      file; do not change `_FakeCursor`) and assert `4`;
      (g) loader where the fifth read raises: `_NthCallRaisingConn(results=[
      tuple(_deploy_row().values()), tuple(utd.values()), ({"chunk_size": 500},), None],
      raise_on_call=5)` with `utd` as in (e); the deployment panel is available, the
      knowledge-base panel is available with `status == "up_to_date"`, `config == {}`,
      `drift == []`, and `corpus_config_available is False`.
      Watch (a), (c), (d), (e) and (g) fail on the unmodified code, then implement D2–D3
      and make all tests in `tests/unit/test_status_provenance.py` and
      `tests/unit/test_status_template_render.py` pass. Do NOT give `_corpus_run_for` a
      `run is None` arm (design D3): it would be dead, uncovered code.

- [ ] 1.2 Implement design D4 in `src/interfaces/chat_app/templates/status.html`, test
      first, one commit (`fix: name the run that embedded the corpus on the status board`).
      Add tests at the end of `tests/unit/test_status_template_render.py`:
      (a) a panel built from an `up_to_date` run (`completed_at=FINISHED`) with an
      `updated` `corpus_run` (`completed_at=STARTED`) renders
      `"From the last run that embedded documents, completed 2026-09-22 19:49 UTC"`;
      (b) a panel built from an `updated` run does not render
      `"From the last run that embedded documents"`;
      (c) a panel built from an `up_to_date` run with `corpus_run=None` renders
      `"Configuration at ingest unknown"` and `"drift not evaluated"`, does not render
      `"No recorded run"`, and does not render `"configuration changed after"`;
      (d) a panel built from an `updated` run does not render
      `"Configuration at ingest unknown"`;
      (e) a panel built from an `up_to_date` run (snapshot `{"categorization": False,
      "chunk_size": 500}`) with an `updated` `corpus_run` (snapshot `{"categorization": False,
      "chunk_size": 1000}`) and `current_snapshot={"categorization": False, "chunk_size":
      500}` renders `"configuration changed after the corpus was built"` and does not render
      `"configuration changed after this ingest"`.
      Watch (a), (c) and (e) fail. Then add the two template blocks and change the drift-box
      title to `⚠ configuration changed after the corpus was built` (design D4). Change the
      two existing assertions that name the old title — line 116 (`... in html`) and line
      230 (`... not in html`) — to `"configuration changed after the corpus was built"`;
      change nothing else in those two tests. Make the whole file pass.

## 2. Docs and publish

- [ ] 2.1 Make every design D5 edit in `docs/docs/services.md` (lines 86, 91 (two edits), 92,
      97, plus the two new sentences; Knowledge Base panel section only; no other section
      changes). Then `grep -n "after this ingest\|config are from the last successful run\|drift until the next ingest\.\|No recorded run" docs/docs/services.md`
      must print nothing. Tick this box, one commit
      (`docs: the status board config comes from the run that embedded the corpus`).
- [ ] 2.2 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat` lists only `src/interfaces/chat_app/status_provenance.py`,
      `src/interfaces/chat_app/templates/status.html`,
      `tests/unit/test_status_provenance.py`, `tests/unit/test_status_template_render.py`,
      `docs/docs/services.md`, and files under
      `openspec/changes/fix-issue-697-drift-baseline/`. Push with
      `git push -u origin fix/issue-697-drift-baseline`. Write the PR body to
      `/tmp/pr-697.md` with `Closes #697` in the body (never the title), a per-task
      summary, a line "Out of scope: partial `updated` runs, tracked in #703", and a
      "Post-merge operator check" line: after a redeploy, change no documents, let one
      ingest run `up_to_date`, and confirm `/ssb/status` shows "From the last run that
      embedded documents" under the config table. Also list in the PR body: the drift-box
      title changed to "configuration changed after the corpus was built", and two existing
      template-test assertions changed with it. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-697-drift-baseline --title "status board: compare drift against the run that embedded the corpus" --body-file /tmp/pr-697.md`.
      This task changes only this checkbox; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-697-drift-baseline` prints the PR.
