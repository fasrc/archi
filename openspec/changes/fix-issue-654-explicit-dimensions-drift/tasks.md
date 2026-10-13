## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-654-explicit-dimensions-drift` exists, cut from `origin/dev`
      at `0ed50a23`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-654-explicit-dimensions-drift --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does.
- [x] 0.3 Every design decision is made in `design.md` (D1–D5). Follow the design; where
      the issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing tests,
  run them and watch them fail, make them pass, then commit — all in one task.
- Each of tasks 1.1 and 2.1 ends with exactly one commit (tick its checkbox in this file in
  the same commit).
- Before you add a test, `grep -n 'def test_' tests/unit/test_ingest_provenance.py` and
  pick a name that is not already used. No linter runs, so a duplicate `def test_...`
  silently replaces the older test.
- Add new tests at the END of `tests/unit/test_ingest_provenance.py`. After the insert,
  `git diff` the file and confirm that the last existing test
  (`test_compare_embedding_dimensions_drift_when_model_changed`) still ends with its own
  `assert "embedding_dimensions" in drift_keys`.
- Do not delete an existing test. Edit an existing test only where design D4 says so (three
  tests, one change each). After the commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After the
  commit, `git status --porcelain` must be empty.
- Never bypass the commit hook. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit `src/interfaces/chat_app/app.py`, `src/interfaces/chat_app/status_provenance.py`,
  any template, or any control-plane file. Under `docs/`, edit only the line-91 bullet in
  `docs/docs/services.md` that task 2.1 names. The PR body goes in `/tmp`, never in the
  repo. Do not add an entry to `docs/questions.md`.

## 1. Record the explicit marker and gate the legacy skip on it

- [x] 1.1 In `tests/unit/test_ingest_provenance.py`, add tests, then implement design
      D1–D3 in `src/utils/ingest_provenance.py`, one commit
      (`fix: report drift when an explicit embedding dimension is removed`). New tests:
      (a) the issue's repro: `at_ingest = build_ingest_config_snapshot({"embedding_name":
      "OpenAIEmbeddings", "embedding_class_map": {"OpenAIEmbeddings": {"dimensions": 384}}})`,
      `current = build_ingest_config_snapshot({"embedding_name": "OpenAIEmbeddings"})`;
      `compare_ingest_config(current, at_ingest)` contains exactly one entry with key
      `embedding_dimensions`, and that entry equals
      `{"key": "embedding_dimensions", "current": 1536, "at_ingest": 384}`;
      (b) snapshot marker: `True` (and `is True`) for
      `{"OpenAIEmbeddings": {"dimensions": 384}}`, `False` (and `is False`) for
      `{"OpenAIEmbeddings": {}}`, `False` for `build_ingest_config_snapshot({})`, and `True`
      for `{"OpenAIEmbeddings": {"dimensions": None}}`;
      (c) only the marker changes: at-ingest config with
      `{"OpenAIEmbeddings": {"dimensions": 1536}}`, current config with
      `{"OpenAIEmbeddings": {}}`, both `embedding_name: OpenAIEmbeddings`;
      `compare_ingest_config(...) == []`;
      (d) a legacy row with a marker-free snapshot whose model is unchanged but whose
      dimensions are not the old default still reports drift: at-ingest built for
      `OpenAIEmbeddings` with `embedding_dimensions` overridden to 768 and the marker key
      popped; current built for `OpenAIEmbeddings`; the drift contains
      `embedding_dimensions`.
      Watch (a) and (b) fail on the unmodified code. Test (c) passes on the unmodified code
      (no marker exists yet); it guards D3. Implement D1 first, run (c) and watch it fail
      with an `embedding_dimensions_explicit` drift entry, then implement D2–D3 and make the
      three existing-test edits of design D4. Confirm that
      `test_compare_no_embedding_dimensions_drift_for_pre_fix_row_with_old_default` (after
      its D4 edit), `test_compare_embedding_dimensions_drift_when_current_is_not_model_default`,
      `test_compare_embedding_dimensions_drift_when_model_changed`,
      `test_snapshot_covers_exactly_the_declared_key_set`, and every test in
      `tests/unit/test_status_provenance.py` pass. Update the comment above the skip in
      `compare_ingest_config` so it says the skip applies only to a snapshot without
      `embedding_dimensions_explicit`.

## 2. Docs and publish

- [x] 2.1 Make the design D5 edits in `docs/docs/services.md` (two sentence
      replacements in the line-91 "Ingest configuration (as used at ingest)" bullet; no
      other line in that file changes), tick this box, one commit (`docs: explicit embedding dimensions now show drift`).
- [x] 2.2 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Confirm
      `git diff origin/dev --stat` lists only `src/utils/ingest_provenance.py`,
      `tests/unit/test_ingest_provenance.py`, `docs/docs/services.md`, and files under
      `openspec/changes/fix-issue-654-explicit-dimensions-drift/`. Push with
      `git push -u origin fix/issue-654-explicit-dimensions-drift`. Write the PR body to
      `/tmp/pr-654.md` with `Closes #654` in the body (never the title), a per-task
      summary, and a "Post-merge operator check" line: after a redeploy and one ingest run,
      the `/ssb/status` Knowledge Base config table shows an `embedding_dimensions_explicit`
      row. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-654-explicit-dimensions-drift --title "status board: report drift when an explicit embedding dimension is removed" --body-file /tmp/pr-654.md`.
      This task changes only this checkbox; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-654-explicit-dimensions-drift` prints the PR.

## 3. Post-PR verification fix

- [ ] 3.1 In `tests/unit/test_ingest_provenance.py`, add
      `at_ingest.pop("embedding_dimensions_explicit")` to
      `test_compare_embedding_dimensions_drift_when_current_is_not_model_default` and
      `test_compare_embedding_dimensions_drift_when_model_changed`, so both reach the
      legacy-skip branch and pin its `model unchanged` and `now == model default` terms.
      Run `bash scripts/gate.sh`, tick this box, one commit
      (`test: pin the legacy dimension skip terms`), and push to the PR branch.
