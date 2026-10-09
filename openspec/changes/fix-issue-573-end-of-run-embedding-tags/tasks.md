## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-573-end-of-run-embedding-tags` exists, cut from `origin/dev`
      at `0189e2dd`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-573-end-of-run-embedding-tags --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does. It is already green.
- [x] 0.3 The gap is measured at `0189e2dd`:
      `grep -rn "embedding_tags" src/ scripts/` prints nothing, and no code reads the
      chunk tags after the start guard (`collection_readiness()`,
      `src/utils/benchmark_provenance.py:990`).
- [x] 0.4 Every design decision is made in `design.md` (D1–D8): the change rule (a foreign
      tag or more untagged chunks), the shared helper pair, the harness and QA end readings,
      absent-stays-absent for old QA manifests, the consumers and the report, the
      `compare_runs` refusal with no flag (arms, noise replicates, QA runs), and the
      archive refusal in single-arm and `--sweep` modes. Follow the
      design; where the issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
  Write the failing test, watch it fail, make it pass, then commit — all in one task.
- **Every task except 7.1 ends with exactly one commit** (tick its checkbox in this file in
  the same commit). Do not add a task that changes no file.
- Do not implement a later task's behaviour early. Each task's red must still fail when that
  task starts.
- Additive schema only: never rename or remove an existing key in any artifact. Never put
  the tag into the corpus fingerprint digest.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not already
  used. No linter runs, so a duplicate `def test_...` silently replaces the older test.
- Never append a test at the very end of an existing file without checking the diff: after
  each insert, `git diff` the file and confirm the test above yours still ends with its own
  `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- A test fixture that runs `git init` must strip every `GIT_*` variable from its env.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Do not edit any control-plane file (`scripts/gate.sh`, `Makefile`, `ralph.conf`,
  `PROMPT.md`, `hooks/**`, `.github/**`, `deploy/**`, `config/**`). Do not write under
  `docs/` except the one edit in task 6. The PR body goes in `/tmp`, never in the repo. Do
  not add an entry to `docs/questions.md`.

## 1. The shared helper

- [x] 1.1 In `tests/unit/test_collection_readiness.py`, add tests, then implement design
      D1 and D2 in `src/utils/benchmark_provenance.py`, one commit
      (`feat: compare the embedding tag state at both ends`). Assert:
      (a) `live_embedding_tag_state(pool, config)` returns
      `{"embedding_model_tags": [...sorted], "untagged_chunk_count": n}` from the same
      `_READINESS_QUERY`, scoped to the config's collection, and raises when the read raises;
      (b) `embedding_tags_unchanged(start, end)` with `start` =
      `{"embedding_model": "m1", "untagged_chunk_count": 0, ...}` returns `True` for end
      `(["m1"], 0)` and for end `([], 0)`; `False` for end `(["m1", "m2"], 0)`, for end
      `(["m2"], 0)`, and for end `(["m1"], 3)`; with start untagged 5, `True` for end
      `(["m1"], 2)` (fewer untagged is not a change) and for end `(["m1"], 5)`;
      (c) it returns `None` when `start` is `None`, lacks `embedding_model` or
      `untagged_chunk_count`, has a `bool` untagged count, or when `end` is a marker string,
      `None`, or a dict with a `bool` untagged count.
      `collection_readiness` and `retrieval_record` do NOT change; no existing assertion in
      this file changes. If the real-SQL tests (`:174`, `:190`) use a `pg` fixture, add one
      real-SQL case for `live_embedding_tag_state` there too.

## 2. Harness end reading

- [x] 2.1 In `tests/unit/test_benchmark_corpus_fingerprint.py`, add tests, then implement
      design D3 in `src/bin/service_benchmark.py`, one commit
      (`feat: harness records the end-of-arm tag state`). Assert, by calling
      `ResultHandler.handle_results` the way the existing identity test at `:340` does,
      with the tag reading patched: an unchanged state records
      `embedding_tags_unchanged_at_endpoints is True` and the end dict; a start record with
      `embedding_model` `m1` and an end reading `["m1", "m2"]` with EQUAL corpus
      fingerprints records `corpus_unchanged_at_endpoints is True` and
      `embedding_tags_unchanged_at_endpoints is False` and logs a warning; an end reading
      that raises records the `<unavailable:` marker and `None`, and the arm keeps its
      scores; `retrieval_identity=None` records `None`. Keep `handle_results`' new lines to
      the reading, the comparison, the warning and the two keys.

## 3. Harness consumers and the per-arm report

- [x] 3.1 In `tests/unit/test_prompt_sweep_leaderboard.py` (for
      `arms_incomparability_reason`, see `:329-340`) and
      `tests/unit/test_leaderboard_corpus_provenance.py` (for the warnings), add tests, then
      implement the first two bullets of design D4 in `src/bin/service_benchmark.py`, one
      commit (`feat: withhold ranks when an arm's tags changed`). Assert: a record with the
      key `False` or `None` gives the tag reason; an absent key or `True` gives the same
      result as today; the leaderboard warning list carries the `False` and the `None`
      messages for the named variant.
- [x] 3.1a (review finding) In `tests/unit/test_leaderboard_corpus_provenance.py`,
      `test_warns_when_an_arm_had_changed_embedding_tags` (~:119) and
      `test_warns_when_embedding_tag_stability_is_unknown` (~:130) check the variant with
      `"a" in w`, which every warning text satisfies ("changed", "stability"). Replace it
      with `"variant 'a'" in w` in both tests. Confirm the stronger check fails if the
      f-string at `src/bin/service_benchmark.py:1328` drops `{name}`, then restore it. One
      commit (`test: pin the variant name in the tag warnings`).
- [x] 3.1b (review finding) `test_a_failed_end_reading_records_the_marker_and_none_and_keeps_scores`
      (`tests/unit/test_benchmark_corpus_fingerprint.py` ~:425) stubs
      `ResultHandler.get_embedding_tag_state` with a ready-made marker, so the wrapper's
      except branch (`src/bin/service_benchmark.py:491-505`) is never asserted. Add a test
      that leaves the wrapper real, makes `live_embedding_tag_state` (as imported in
      `service_benchmark`) raise `RuntimeError("boom")`, and asserts
      `embedding_tags_end` starts with `CORPUS_UNAVAILABLE`,
      `embedding_tags_unchanged_at_endpoints is None`, and the scores are kept. Confirm it
      fails if the except body returns `None`. One commit
      (`test: drive the real tag-read wrapper through a failure`).
- [x] 3.2 In the existing report-provenance tests
      (`tests/unit/test_benchmark_report_provenance_panel.py`,
      `tests/unit/test_benchmark_report_markdown.py`, or
      `tests/unit/test_benchmark_report_html_provenance.py` — use the one whose fixtures
      already exercise `corpus_unchanged_at_endpoints`), add tests, then implement the third
      bullet of design D4 in `src/utils/generate_benchmark_report.py` (`:159`, `:218`,
      `:1193`), one commit (`feat: report flags changed embedding tags`). Assert for both
      the HTML and the Markdown output: `False` shows the alert text; `None` shows
      "unknown"; `True` shows the unchanged line; an artifact without the key shows no tag
      line and is otherwise identical to today's output.

## 4. QA end reading

- [x] 4.1 In `tests/unit/evaluation/qa/test_provenance.py` and
      `tests/unit/evaluation/qa/test_workflow.py`, add tests, then implement design D5 in
      `src/evaluation/qa/provenance.py` and the call sites in
      `src/evaluation/qa/workflow.py` (`:368`, `:597`, `:995-1001`, `:1141`), one commit
      (`feat: qa run records the end-of-run tag state`). Assert in `test_provenance.py`:
      `end_readings` with the search tool and an unchanged state returns both keys with
      `True`; a foreign end tag returns `False`; a failing tag read returns the marker and
      `None` and does not raise; without the search tool both keys are `None` and no reading
      is made; `summary_fields` on a manifest WITHOUT the tag keys returns no tag key, and on
      a manifest with them copies both; `carried_readings` copies the tag keys only when the
      parent has them. Update the existing `end_readings` exact-dict assertions
      (`test_provenance.py:82`, `:90`) to include the two keys. In `test_workflow.py`: use the
      existing fake run fixture to show that `manifest.json` and `summary.json` `provenance`
      carry the two keys after a run with search; update the exact key set at `:295` to what
      that run now records, and widen the `end_readings` stub at `:372-379` to accept
      `identity_before=None`. Change no other existing assertion.

## 5. compare_runs and archive tooling refuse

- [ ] 5.1 In `tests/unit/test_compare_runs.py`, add tests in a new section marked
      `# --- #573: end-of-run embedding tags ---` placed above an existing section marker
      (not at the end of the file), then implement design D7 in
      `scripts/benchmarking/compare_runs.py`, one commit
      (`feat: compare_runs refuses an arm whose tags changed`). Reuse the fixtures of the
      embedding tests at `:854-923`, the noise-replicate tests, and the QA test at `:3392`.
      Assert: an arm recording `False` is refused with the gate exit code and a reason naming
      the arm, also with `--corpus-differs-by-design`; an arm recording `None` (key present)
      is refused; arms without the key, or recording `True`, compare as before; a
      `--noise-runs` replicate recording `False` is refused with the gate exit code, also
      with `--corpus-differs-by-design`; a `--qa-run` with equal readings and the key `False`
      is refused with the tag reason; a QA run without the key joins as before.
- [ ] 5.2 In `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`, add two
      numbered cases after the last existing case (update the numbered case list in the
      header comment), then implement the single-arm part of design D8 in
      `scripts/benchmarking/feature_matrix/archive_run.sh`, one commit
      (`feat: archive_run refuses an arm whose tags changed`). Cases: an artifact with
      `"embedding_tags_unchanged_at_endpoints": false` → exit 2, a `REFUSED:` line naming
      the tag change, no ledger row, pin file unchanged; the same artifact with `true` →
      archived as today. Run the file directly
      (`bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`) before
      the commit; `scripts/gate.sh` also runs it.
- [ ] 5.3 In `tests/unit/test_sweep_tools.py`, add tests, then implement the `--sweep` part
      of design D8 in `scripts/benchmarking/feature_matrix/sweep_tools.py` (`archive`'s
      per-arm loop, next to the fingerprint check at `:377-385`), one commit
      (`feat: sweep archive refuses an arm whose tags changed`). Reuse the fixture the
      existing fingerprint-refusal test uses. Assert: an arm recording `False` or `None`
      raises `SweepError` naming the arm and the tag change, and no pin file is written; an
      arm without the key, or recording `True`, archives as today.

## 6. Docs

- [ ] 6.1 In `docs/docs/interpreting_benchmark_results.md`, next to the
      `corpus_unchanged_at_endpoints: false` entry near line 608, add one entry for
      `embedding_tags_unchanged_at_endpoints: false` (what it means: the collection was
      re-embedded during the arm, so some questions searched vectors of another model or of no recorded model;
      the fingerprint cannot see this because it is model-neutral; `compare_runs` and
      `archive_run.sh` refuse the arm; `null` means not observed). If `mkdocs` is
      installed, run `mkdocs build --strict -f docs/mkdocs.yml` and read its INFO lines for
      a broken anchor; if it is not installed, say so in the commit body. One commit
      (`docs: explain embedding_tags_unchanged_at_endpoints`).

## 7. Publish

- [ ] 7.1 Run `bash scripts/gate.sh` on the tip and confirm it exits 0. Run
      `grep -rn "embedding_tags_unchanged_at_endpoints" src/ scripts/` and confirm it
      prints matches in `service_benchmark.py`, `qa/provenance.py`,
      `generate_benchmark_report.py`, `compare_runs.py`, `archive_run.sh` and
      `sweep_tools.py`. Confirm `git diff origin/dev --stat -- docs/` lists only
      `docs/docs/interpreting_benchmark_results.md`. Push with
      `git push -u origin fix/issue-573-end-of-run-embedding-tags`. Write the PR body to
      `/tmp/pr-573.md` with `Closes #573` in the body (never the title) and the per-task
      summary. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-573-end-of-run-embedding-tags --title "provenance: re-check embedding_model tags at the end of each eval arm and qa run" --body-file /tmp/pr-573.md`.
      This task changes no tracked file; it is complete when
      `gh pr view --repo fasrc/archi fix/issue-573-end-of-run-embedding-tags` prints the PR.
