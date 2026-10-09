## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-565-status-provenance-honesty` exists, cut from `origin/dev`
      at `0189e2dd`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-565-status-provenance-honesty --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D6). Follow the design; where
      the issue body (#565) and the design differ, the design wins (for example: the
      failed-attempt query runs LAST, after the config query, and the dimension parity
      test parses `manager.py` with `ast` because the table is a local variable).

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing test,
  run it and watch it fail, make it pass, then commit — all in one task.
- **Every task except 7.1 ends with exactly one commit** (tick its checkbox in this file
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
- Nothing in `status_provenance.py`, `ingest_run.py`, or `ingest_provenance.py` may raise.
  Keep every new path inside the existing `try` blocks or make it pure and total.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py` or any control-plane file. Do not write
  under `docs/` except the edit in task 6. The PR body goes in `/tmp`, never in the repo.
  Do not add an entry to `docs/questions.md`.

## 1. F1 — recorded embedding dimensions (design D1)

- [x] 1.1 In `tests/unit/test_ingest_provenance.py` add tests: (a) `OpenAIEmbeddings` with
      a class-map entry that has no `dimensions` records 1536; (b) `HuggingFaceEmbeddings`
      with no `dimensions` records 384; (c) an explicit `dimensions` value wins for
      `OpenAIEmbeddings`; (d) an unknown model with no `dimensions` records 384;
      (e) parity: parse `src/data_manager/vectorstore/manager.py` with `ast`, find the
      assignment to `default_dimensions`, `ast.literal_eval` it, and assert it equals
      `_DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL` (fail with a clear message if no such
      assignment is found). Watch (a) and (e) fail. Add the table and the lookup to
      `src/utils/ingest_provenance.py`, and extend the defaults comment block to name the
      origin. One commit (`fix(#565): record per-model default embedding dimensions`).

## 2. F3 — collection-scoped chunk count (design D2)

- [x] 2.1 In `tests/unit/test_ingest_run.py` add tests: (a)
      `collect_ingest_counts(conn, collection_name="x_with_y")` executes chunk SQL that
      contains `metadata->>'collection' = %s` and `metadata->>'collection' IS NULL` and
      binds `("x_with_y",)`; (b) with no `collection_name` the chunk SQL has no `WHERE`
      and binds no params (do not assert byte-identical SQL text: design D2 replaces the
      old constant).
      Widen `_FakeCursor.execute` to record params if it does not already. Add a manager
      test (find the existing `_record_ingest_run` tests with
      `grep -rn '_record_ingest_run' tests/unit`; if none exist, build the manager with
      `__new__` and patch `psycopg2.connect`, `record_ingest_run` and
      `collect_ingest_counts`) that asserts `collect_ingest_counts` receives
      `collection_name=<the manager's collection_name>`. Watch them fail. Implement
      design D2 in `src/utils/ingest_run.py` and the one call site in
      `src/data_manager/vectorstore/manager.py`. Before the commit, run
      `grep -n 'SELECT COUNT(\*) FROM document_chunks"' src/utils/ingest_run.py` and
      confirm it prints nothing. One commit
      (`fix(#565): scope the recorded chunk count to the active collection`).

## 3. F2 — corpus run vs failed attempt (design D3)

- [x] 3.1 In `tests/unit/test_status_provenance.py` add tests: (a) `_SQL_LATEST_INGEST_RUN`
      contains `status IN ('updated', 'up_to_date')`; (b) `build_knowledge_base_panel`
      with a corpus run and a newer `last_failed_at` sets `last_attempt_failed_at` to it;
      (c) with an older `last_failed_at` it is `None`; (d) with no run and a
      `last_failed_at` the panel is unavailable and carries the time; (e) a naive vs aware
      datetime pair does not raise and returns the failed time; (f)
      `load_status_provenance` with four results (deployment, run, config, failed) puts
      the fourth result's time in the view model, and with three results (the existing
      fakes) leaves it `None`. In `tests/unit/test_status_template_render.py` add render
      tests for the available-panel warning and the unavailable-panel line. Watch them
      fail. Implement design D3 in `status_provenance.py` and `status.html`. One commit
      (`fix(#565): never present a failed ingest attempt as the serving corpus`).
- [x] 3.1a Review fix (post-PR check, 2026-10-09). `status.html:370` says "the corpus
      above is from the last successful run". That is false after a partial failure:
      `manager.py:372` commits deletions (`_remove_from_postgres`) before `_add_to_postgres`
      (`manager.py:384`), and the add commits in batches, so a `failed` run can change the
      live corpus. In `tests/unit/test_status_template_render.py` add a render test: the
      failed-attempt warning says the counts are from the last successful run AND that the
      failed attempt can have changed the live corpus; it does not say the corpus above is
      from the last successful run. Watch it fail. Change only the template wording, and
      the same sentence in `design.md` D3. One commit
      (`fix(#565): do not claim a failed attempt left the corpus unchanged`).
- [x] 3.1b Review fix (post-PR check, 2026-10-09). The per-model default from task 1.1
      makes the board show false drift for every row recorded before the fix: an
      `OpenAIEmbeddings` deployment with no `dimensions` rebuilds its current snapshot as
      1536, the stored snapshot says 384, and `compare_ingest_config`
      (`src/utils/ingest_provenance.py:190-218`) reports "configuration changed after this
      ingest". In `tests/unit/test_ingest_provenance.py` add tests: (a) stored
      `{embedding_model: OpenAIEmbeddings, embedding_dimensions: 384}` against current
      `{OpenAIEmbeddings, 1536}` gives no `embedding_dimensions` drift; (b) control: the
      same stored row against current `{OpenAIEmbeddings, 768}` still reports drift;
      (c) control: stored `{HuggingFaceEmbeddings, 384}` against current
      `{OpenAIEmbeddings, 1536}` still reports drift on both keys. Watch them fail. In
      `compare_ingest_config`, skip only the mismatch where the key is
      `embedding_dimensions`, the stored value equals `_DEFAULT_EMBEDDING_DIMENSIONS`, the
      `embedding_model` is the same in both snapshots, and the current value equals
      `_DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL.get(model)`. Must not raise. One commit
      (`fix(#565): do not report pre-fix dimension rows as config drift`).
- [x] 3.1c Review fix (review thread, 2026-10-09). With only failed runs recorded, the
      unavailable headline said "No completed ingest run recorded" while the next line
      said the latest attempt failed (it did complete). In
      `tests/unit/test_status_template_render.py` add a render test: the headline says
      "No successful ingest run recorded" and not "No completed ingest run recorded".
      Update the existing no-record render test's headline assertion to match. Watch it
      fail. Change only the template headline. One commit
      (`fix(#565): say no successful ingest run in the unavailable headline`).
- [ ] 3.1d Review fix (review thread, 2026-10-09). In `load_status_provenance` the four
      reads share one `try`. If the config query or `build_ingest_config_snapshot` raises,
      the failed-attempt query never runs, so a newer failed attempt is hidden; in
      PostgreSQL the failed statement also aborts the transaction, so a later read would
      fail too. In `tests/unit/test_status_provenance.py` add tests: (a) the config query
      raises on the third `execute` and the fourth result's time still reaches
      `last_attempt_failed_at`; (b) the connection's `rollback` is called after that error;
      (c) `build_ingest_config_snapshot` raising still leaves the failed time set and the
      config unavailable. Watch them fail. Isolate the config read in its own `try`; on an
      error roll the connection back (guarded) and go on to the failed-attempt read. Keep
      the read order. Update design D3/D4 to match. One commit
      (`fix(#565): keep a newer failed attempt visible when the config read fails`).

## 4. F4 — unavailable current config (design D4)

- [x] 4.1 In `tests/unit/test_status_provenance.py` add tests: (a)
      `build_knowledge_base_panel(run, None)` gives `drift == []` and
      `current_config_available is False`; (b) with a mapping it gives
      `current_config_available is True` and drift as today; (c) `load_status_provenance`
      whose cursor raises on the third `execute` (the config query) keeps the deployment
      and run panels available and gives `drift == []`, `current_config_available is False`;
      (d) a config query that returns no row does the same. Add a small cursor fake for
      (c) that raises only on a chosen call number; do not change `_FakeCursor`'s existing
      behaviour. In `tests/unit/test_status_template_render.py` add a render test: the
      unavailable text renders and the drift warning title does not. Watch them fail.
      Implement design D4. One commit
      (`fix(#565): report unavailable current config instead of drift on every key`).

## 5. F5 — pin mismatch (design D5)

- [x] 5.1 In `tests/unit/test_status_provenance.py` add a parametrised test:
      `build_deployment_panel` with `pin_matched` `True`, `False`, `None` gives
      `pin_mismatch` `False`, `True`, `False`; the unavailable panel gives `False`. In
      `tests/unit/test_status_template_render.py` add render tests: `pin_matched=None` with
      dirty paths renders no "not the pinned commit" sentence; `pin_matched=False` does.
      Watch them fail. Implement design D5 and change `status.html` line
      `{% if not dep.pin_matched %}` to `{% if dep.pin_mismatch %}`. One commit
      (`fix(#565): say "not the pinned commit" only on a recorded mismatch`).

## 6. F6 — docs (design D6)

- [ ] 6.1 Edit `docs/docs/services.md` per design D6. Describe only fields that exist:
      `grep -n '"' src/interfaces/chat_app/status_provenance.py` and check each field you
      name. If `mkdocs` is installed, run `mkdocs build --strict -f docs/mkdocs.yml` and
      read its INFO lines for a broken anchor; if it is not installed, say so in the commit
      body. One commit (`docs(#565): document the status board provenance panels`).

## 7. Publish

- [ ] 7.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Run each grep in
      issue #565's "Commands" section and confirm the stated results. Confirm
      `git diff origin/dev --stat -- docs/` lists only `docs/docs/services.md`. Push with
      `git push -u origin fix/issue-565-status-provenance-honesty`. Write the PR body to
      `/tmp/pr-565.md` with `Closes #565` in the body (never the title), one line per
      finding F1–F6 with its commit, and a "Post-merge operator check" line: after a
      redeploy and one ingest, open `/ssb/status` and confirm the embedding dimensions
      row matches the configured embedder. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-565-status-provenance-honesty --title "status board: honest ingest and deployment provenance (six #542 findings)" --body-file /tmp/pr-565.md`.
      This task changes no tracked file; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-565-status-provenance-honesty` prints the PR.
