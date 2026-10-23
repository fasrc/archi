Rules for every task:
- The design of record is `openspec/changes/offload-ragas-judge-to-slurm/`. This change
  only drives PR 2 of it (issue #731). Read `design.md` here (D1–D11) before task 1.1.
  Where this change and the parent differ, this change wins; task 7.1 corrects the parent.
- Each checkbox is ONE commit. Inside each task: write the failing test first, run it and
  watch it FAIL, then write the minimum code, then watch it pass. Never commit a red test.
- Run `bash scripts/gate.sh` before each commit. Commit only when it is green. Never use
  `--no-verify`. Add no `Co-Authored-By` or session trailers. If patch coverage is below
  80%, read the `diff-cover` report and add tests for the uncovered lines; never lower the
  threshold.
- Keep new logic in `src/utils/judge_bundle.py`, `src/utils/exclusive_link.py` and
  `src/utils/benchmark_provenance.py`. The lines in `src/bin/service_benchmark.py` are
  thin call sites.
- Put new tests in NEW files with unique test names. Do not append to an existing test
  file. The only allowed edits to an existing test file are the two stubs of design D4 in
  `tests/unit/test_benchmark_resilience.py` (task 3.1) and line 91 of
  `tests/unit/test_base_config_judge_render.py` (task 3.1c).
- Patch `ResultHandler.results`, `ResultHandler.metadata`,
  `ResultHandler.category_map_records_by_arm` and `src.bin.service_benchmark.OUTPUT_DIR`
  with `monkeypatch` in every test that writes artifacts. A test that reaches the A/B or
  leaderboard code also patches `ResultHandler.ab_comparison` (`{}`),
  `ResultHandler.ab_comparisons` (`[]`), `ResultHandler.leaderboard` (`{}`) and
  `ResultHandler._corpus_snapshot_id` (`None`), as
  `tests/unit/test_benchmark_dump_artifacts.py:46-48` does. Use `tmp_path` for files.
- Never let a test pass on zero items: assert exact file names and exact counts, not a
  loop over a glob that can be empty.
- Do not change the inline path's result JSON: an inline arm gets no new key.
- No test needs a GPU, Slurm, network access, or a real `pip freeze` run.
- Do not start #732 (scorer, claims, checkpoints, `judge_identity`, merge) or #733
  (Slurm, gates, docs). Do not write task 2.7 or `publish.py --status`.
- Do not edit `config/**`, `deploy/**`, `.github/workflows/**`, `scripts/gate.sh`,
  `hooks/**`, `Makefile` or `Containerfile`.

## 1. Judge mode and the one-mode rule (parent task 2.1)

- [x] 1.1 Create `tests/unit/test_judge_mode.py` with tests for `parse_judge_mode` and
      the mode part of `plan_judge_run` (design D2 and D3 steps 1–3): absent gives
      `inline`; `"inline"` gives `inline`; `"deferred"` gives `deferred`; `""`,
      `False`, `None` present in the dict, `"Deferred"` and `"offline"` raise
      `ValueError` that names the value and both allowed values; over two config files
      in `tmp_path`, one with no `judge_mode` and one with `inline`, the plan is
      `inline` with `arm_count == 2`; one `deferred` and one with no key raises
      `JudgeRunRefused` whose message names both paths and both modes; a config with an
      invalid value raises and names that path. Run the file and watch it FAIL. Then
      create `src/utils/judge_bundle.py` with `parse_judge_mode`, `JudgeRunRefused`, a
      frozen `JudgePlan` dataclass and `plan_judge_run(config_paths, *, package_dir,
      git_info_path, package_digest_fn=None)` (steps 1–3 only; a deferred plan returns
      `JudgePlan(mode="deferred", arm_count=n)` until task 3.2). In
      `Benchmarker.__init__`, set `self.judge_plan = plan_judge_run(...)` right after
      `self.all_config_files = self.get_all_configs(configs)` and before `"FINISHED"`
      is appended (pass `package_dir=PACKAGE_DIR`,
      `git_info_path=EXTRA_METADATA_PATH`). Add a test that builds a real `Benchmarker`
      over a `tmp_path` configs folder with a mixed sweep (set the `container_name`
      env var with `monkeypatch`) and asserts `JudgeRunRefused` before
      `load_new_configuration` is called (replace it on the class with a recorder via
      `monkeypatch`). Run the file and watch it pass. Tick parent task 2.1 in
      `openspec/changes/offload-ragas-judge-to-slurm/tasks.md`. Gate, then commit
      (message: `parse judge_mode and refuse a sweep that mixes modes`).

## 2. Render the new keys (parent task 2.2)

- [x] 2.1 Create `tests/unit/test_base_config_judge_render.py`. Copy the `_render`
      helper pattern of `tests/unit/test_base_config_benchmark_render.py:20-29` into it
      (do not import from that test module). Tests: with none of the three keys set, the
      rendered `ragas_settings` has no `judge_mode`, `judge_bundle_dir` or
      `evaluator_model_revision` key; each key renders when set (`"deferred"`,
      `"bundles/x"`, `"0123abc"`); `judge_mode: false` renders as `False`, and
      `parse_judge_mode` refuses it; a revision that YAML 1.1 would retype (for example
      `"1e3"` or `"null"`) keeps its string type. Run the file and watch it FAIL. Then
      add the three keys to `src/cli/templates/base-config.yaml` in `ragas_settings`,
      after `evaluator_provider_mode` (`:123-129`), each in that block's shape:
      `{%- if <key> is defined and <key> is not none %}` ... `| tojson` ...
      `{%- endif %}`, with a one-line comment for each. Run the file and the existing
      `tests/unit/test_base_config_benchmark_render.py`, and watch both pass. Tick parent
      task 2.2. Gate, then commit (message: `render judge_mode, judge_bundle_dir and
      evaluator_model_revision when set`).

## 3. Fixed basename and start refusals (parent tasks 2.2a and 2.2b)

- [x] 3.1 Create `tests/unit/test_benchmark_result_basename.py`. Tests (design D4):
      with a two-arm state, `dump_artifacts(Path("bench"), timestamp="20260101_000000")`
      writes exactly `bench-20260101_000000.json`, `bench-20260101_000000_report.md`,
      `bench-20260101_000000_category_map_1.tsv` and
      `bench-20260101_000000_category_map_2.tsv`, and also
      `sorted(p.name for p in tmp_path.glob("*_category_map_*.tsv"))` equals those two
      names. Build the state yourself, because the fixture of
      `tests/unit/test_benchmark_dump_artifacts.py` writes NO TSV
      (`dump_category_maps` skips an entry with no `category_map_sha256_end`,
      `src/bin/service_benchmark.py:972-973`): with `monkeypatch`, set
      `ResultHandler.results` to two entries in the shape of that fixture's entry
      (`tests/unit/test_benchmark_dump_artifacts.py:30-42`), each with an added
      `"category_map_sha256_end": "sha256:x"`; set
      `ResultHandler.category_map_records_by_arm` to two lists of strings (the shape
      `category_map_text` takes, `src/utils/benchmark_provenance.py:489`), for example
      `[["a\tA"], ["b\tB"]]`; and patch `metadata`,
      `ab_comparison`, `ab_comparisons`, `leaderboard`, `_corpus_snapshot_id` and
      `OUTPUT_DIR` as the rules say. `dump_artifacts(Path("bench"))`
      with no timestamp still works; `run()` on a `_RunStub`-style subclass (pattern of
      `tests/unit/test_benchmark_resilience.py:494-523`) with two arms calls
      `ResultHandler.result_timestamp` exactly once, before the first `_process_config`,
      sets `self.result_basename`, and passes that timestamp to `dump_artifacts` as
      `timestamp=`. Run the file and watch it FAIL. Then add
      `ResultHandler.result_timestamp()`, the `timestamp=None` keyword of
      `dump_artifacts`, and the two `run()` call sites. Change the two stubs at
      `tests/unit/test_benchmark_resilience.py:519` and `:571` to
      `lambda name, **kwargs: None`, and nothing else in that file. Run this file,
      `tests/unit/test_benchmark_resilience.py`, `tests/unit/test_benchmark_dump_artifacts.py`
      and `tests/unit/test_benchmark_category_map.py`, and watch all pass. Tick parent
      task 2.2a. Gate, then commit (message: `fix the result basename before the first
      arm`).

- [x] 3.1a (Review findings: Codex P2 on PR #747, and the wrap-up check.) Design D3 says
      `plan_judge_run` returns a `JudgePlan` or raises `JudgeRunRefused`, "a `ValueError`
      subclass". At 21e88806, `src/utils/judge_bundle.py:18` derives `JudgeRunRefused`
      from `Exception`, and `_read_mode` (`:52-56`) re-raises an invalid `judge_mode` as a
      plain `ValueError`. `tests/unit/test_judge_mode.py:100` accepts either type, so it
      cannot see this. Create `tests/unit/test_judge_refused_is_value_error.py` with four
      tests: (1) `issubclass(JudgeRunRefused, ValueError)` is true; (2) over two config
      files in `tmp_path` (one `deferred`, one with no `judge_mode`), `plan_judge_run(...)`
      inside `try: ... except ValueError as exc:` catches the error, and
      `type(exc) is JudgeRunRefused`; (3) a config file with `judge_mode: Deferred` raises
      `JudgeRunRefused` (use `pytest.raises(JudgeRunRefused)`, not a tuple) whose message
      names that file path and `'Deferred'`; (4) a config file whose `ragas_settings` holds
      `judge_mode: null` (the key present, the value null) raises `JudgeRunRefused` whose
      message names that file path (design D2: a null that reached the file is refused).
      Name the config files `a.yaml` and `b.yaml`, so that a path check cannot pass on a
      mode word in the file name. Run the file and watch all four FAIL. Then change the
      base class to `ValueError`, change the re-raise in `_read_mode` to
      `raise JudgeRunRefused(f"{config_path}: {exc}") from exc`, and change the
      `plan_judge_run` docstring line "Raises ValueError if any config holds an invalid
      judge_mode" to name `JudgeRunRefused`. `parse_judge_mode` keeps raising a plain
      `ValueError`. Run this file and `tests/unit/test_judge_mode.py`, and watch both pass.
      Gate, then commit (message: `refuse an invalid judge_mode with JudgeRunRefused, a
      ValueError`).

- [x] 3.1b (Review finding, Codex P2 on PR #747.) Design D4 says `run()` takes the result
      timestamp as its FIRST statement. At `src/bin/service_benchmark.py:2455` it comes
      after `wait_for_ingestion_completion()`, which can block for hours, so the file name
      records the end of the ingest wait. Create
      `tests/unit/test_benchmark_basename_before_ingest.py`. Copy the `_RunStub` and the
      `monkeypatch` setup of
      `tests/unit/test_benchmark_result_basename.py::test_run_records_timestamp_before_first_process_config`
      (copied, not imported), with one change: `wait_for_ingestion_completion` appends
      `"wait_for_ingestion"` to `calls` and returns `0.0`. Assert
      `calls[:2] == ["result_timestamp", "wait_for_ingestion"]` and
      `calls.count("result_timestamp") == 1`. Run the file and watch it FAIL. Then move the
      two lines (`_result_ts = ...` and `self.result_basename = ...`) to the top of
      `run()`, before `wait_for_ingestion_completion()`. Change no other line. Run this
      file and `tests/unit/test_benchmark_result_basename.py`, and watch both pass. Tick
      parent task 2.2a (task 3.1 did not tick it). Gate, then commit (message: `take the
      result timestamp before the ingest wait`).

- [x] 3.1c (Review findings from the wrap-up check.) Three render checks of task 2.1 can
      never fail. (a) `tests/unit/test_base_config_judge_render.py:88-91` calls
      `parse_judge_mode({"judge_mode": ragas["judge_mode"]})`, but `parse_judge_mode`
      takes the bare value (design D2 now says so), so any dict is refused and the test
      passes whatever the template renders. (b) No test pins `| tojson` on
      `judge_bundle_dir` (`src/cli/templates/base-config.yaml:136`): with it removed, a
      folder named `2026` or `null` loads as an int or `None`, and every test stays green.
      (c) PyYAML loads a bare `1e3` as a string, so
      `test_revision_1e3_keeps_string_type` cannot fail. These tests pin behavior that is
      already correct, so they pass at once; prove each one can fail instead (below).
      In `tests/unit/test_base_config_judge_render.py`, change only line 91 to
      `parse_judge_mode(ragas["judge_mode"])`. This is the one allowed edit to that file.
      Create `tests/unit/test_base_config_judge_render_types.py`. Copy the `_render` and
      `_render_ragas` helpers of that file (copied, not imported). Tests: a rendered `"deferred"` parses to
      `"deferred"`; `judge_bundle_dir` `"2026"` and `"null"` each render as that string
      (`type(...) is str`); `judge_mode` `"null"` renders as the string `"null"`, and
      `parse_judge_mode` refuses it; `evaluator_model_revision` `"0123456"` (an
      all-digit short SHA) renders as that string. Prove that each new test can fail:
      in a scratch edit, remove `| tojson` from the `judge_bundle_dir` line, run the new
      file, and watch the two `judge_bundle_dir` tests FAIL; do the same for the
      `evaluator_model_revision` line and the revision test; make `parse_judge_mode`
      accept `False`, and watch the edited line-91 test FAIL. Restore each scratch edit
      (`git diff src/` must be empty, except the comment fix below). Also change the
      template comment of `evaluator_model_revision` (`base-config.yaml:139`): replace the
      words "Git revision of the judge model image" with "Revision of the judge model
      (evaluator_model)", and keep the rest of the line, because D2 defines it as the
      model's revision, not an image's. Run both render test files and watch them pass.
      Gate, then commit (message: `make the judge render tests able to fail`).

- [x] 3.2 Create `tests/unit/test_judge_start_refusal.py`. Tests (design D3 steps 4–7,
      D7, D9): `package_digest` is order-independent, ignores blank lines and
      surrounding spaces, and returns `sha256:<64 hex>`; `collect_package_digest` with a
      stub `run` returns the digest of the stub output, and returns
      `digest: None` with an `<unavailable: ...>` source for a non-zero exit, an
      `OSError`, a `subprocess.TimeoutExpired` and empty output; `plan_judge_run` on two
      deferred configs with a stub `package_digest_fn` and a `package_dir` that holds
      one `.py` file returns `code_version` with a digest and the stub package digest;
      a `package_dir` with no module files refuses and quotes the `<unavailable`
      source; an unavailable package digest refuses; an empty `evaluator_model`, an
      empty or missing `evaluator_model_revision`, and a `judge_bundle_dir` of `""`,
      `"/abs"` or `"a/../.."` each refuse and name the config; two deferred configs, one
      with no `judge_bundle_dir` and one with `"x"`, refuse, and the message names both
      paths and both resolved values (`judge_bundles` and `x`); two configs with `"x"`
      and `"x/"` are accepted, and the plan's `bundle_dir` is `x`; an all-inline sweep never
      calls `package_digest_fn` (pass one that raises). For `RESULT_COMPLETE`: a
      `_RunStub`-style deferred `run()` (set `judge_plan` to a deferred `JudgePlan`, and
      `OUTPUT_DIR` to `tmp_path`) with a `dump_artifacts` stub that asserts the marker
      does not exist yet, leaves `<result_basename>.RESULT_COMPLETE` after `run()`
      returns; an inline `run()` leaves no marker; a `dump_artifacts` that raises leaves
      no marker. Run the file and watch it FAIL. Then add `package_digest` and
      `collect_package_digest` to `src/utils/benchmark_provenance.py`, steps 4–7 to
      `plan_judge_run` (when `package_digest_fn` is `None`, as in the
      `Benchmarker.__init__` call, use `collect_package_digest`), `write_result_complete` to `judge_bundle.py`, and its call site
      after `dump_artifacts` in `run()`. Watch the file pass. Tick parent task 2.2b.
      Gate, then commit (message: `refuse a deferred run that no scorer could match`).

## 4. The bundle writer (parent task 2.3)

- [x] 4.1 Create `tests/unit/test_judge_bundle_writer.py`. Tests (design D5): over 3
      rows and keys, `write_judge_bundle` makes `<root>/<basename>-arm0/` with a
      3-line `rows.jsonl` whose lines, without `key`, equal the input rows and keep
      full contexts; the manifest has exactly the keys of the D5 table with the right
      values (compute the expected `rows_sha256` in the test from the file bytes;
      `ragas_run_config_kwargs` equals `ragas_run_config_kwargs(settings, verbosity)`;
      `batch_size` 4 when the settings say 4, `None` when they say `false`;
      `enabled_metrics` in `RAGAS_METRIC_NAMES` order with unknown names dropped);
      `BUNDLE_COMPLETE` is created after both other files are complete (wrap the
      module's file-write function with `monkeypatch` and record the order and the
      `fsync` calls); a second call for the same folder raises and changes no byte;
      mismatched `rows`/`keys` lengths raise `ValueError` and write nothing; zero rows
      give an empty `rows.jsonl`, `row_count: 0` and `BUNDLE_COMPLETE`; a row whose
      `reference` holds a lone surrogate (`"abc\ud83d"`) and whose `response` holds
      non-ASCII text writes without error, the file is pure ASCII, and `json.loads` of
      the line without `key` equals the input row; the return value
      is `{"folder": ..., "digest": <rows_sha256>}`. Run the file and watch it FAIL.
      Then write `write_judge_bundle` in `src/utils/judge_bundle.py`. Watch it pass.
      Tick parent task 2.3. Gate, then commit (message: `write the judge bundle for a
      deferred arm`).

## 5. The deferred call site (parent tasks 2.4 and 2.5)

- [x] 5.1 Create `tests/unit/test_benchmark_deferred_arm.py`. Build the bench with
      `object.__new__(Benchmarker)` (pattern of
      `tests/unit/test_ragas_scoring_characterization.py`), set `config`,
      `benchmarking_configs`, `judge_plan` (deferred, with a code version and a package
      digest, and `arm_count` 2), `result_basename`, `_arm_index = 1` (not 0, so that a
      branch that hard-codes arm 0 fails), and replace `get_ragas_llm_evaluator`,
      `get_ragas_embedding_model` and `get_ragas_results` with functions that fail the
      test if called. Tests (design D6, D8): `_process_config` over 3 scorable questions
      (stub `_answer_and_score_question`) writes exactly one bundle folder, named
      `OUTPUT_DIR/judge_bundles/<result_basename>-arm1` (assert the exact name), whose
      manifest has `arm_index` 1, `arm_count` 2 and the bench's `result_basename`, and
      whose `rows.jsonl` lines equal the `dataset_result` records by key; `total_results` has no `aggregate_*` and no `*_scored` key;
      `_ragas_scored is False`; `_judge_bundle` holds the folder and the digest; a plan
      whose `bundle_dir` is `x` writes under `OUTPUT_DIR/x/`; an arm whose answers all
      failed writes a bundle with zero rows and no `n/a` aggregates; with `judge_plan`
      absent or inline, `_process_config` calls `get_ragas_results` as before and writes
      no bundle; `handle_results(..., judge_bundle={...})` records
      `judge_status: "pending"`, `judge_bundle`, and `null` `ragas_effective_settings`
      and `ragas_judge_identity_effective`, and an inline call records neither new key
      (reuse the `handle_results` call setup of
      `tests/unit/test_benchmark_report_records_running_config.py`, copied, not
      imported: the autouse `_reset_results` fixture `:28-39`, `_write` `:44-48`,
      `_ragas_config` `:77-85`, and the `ragas_scored=False` call shape of
      `test_unjudged_ragas_arm_records_no_settings` `:238` and
      `test_judge_identity_is_none_when_not_scored` `:299`); a two-arm deferred `run()`
      on a `_RunStub`-style subclass passes arm indexes 0 and 1 and each arm's bundle to
      `handle_results`. In that `run()` test, the `handle_results` recorder also appends
      one pending-arm record to `ResultHandler.results` for each call (keys:
      `configuration` with a `services.benchmarking` block that has a `name`,
      `single_question_results` with the same question key in both arms and no metric
      key, `total_results` with no `aggregate_*` key, `judge_status: "pending"`,
      `judge_bundle`, and `None` for `ragas_effective_settings` and
      `ragas_judge_identity_effective`), so the A/B and leaderboard blocks run (they need
      2 or more records: the two `if len(ResultHandler.results) >= 2:` lines,
      `src/bin/service_benchmark.py:2503` and `:2523` at 21e88806). Assert
      `len(ResultHandler.ab_comparisons) == 1`, and that `ResultHandler.leaderboard` has
      two rows, each with every metric `None` and `incomplete` set to `True`.
      Also test `judge_status_of`: `{}` and an arm without the key give `inline`, and
      `pending` and `scored` pass through. Run the file and watch it FAIL. Then add the
      deferred branch to `_process_config`, the `_judge_bundle` reset, the `_arm_index`
      counter and the `judge_bundle=` keyword in `run()`, the keyword in
      `handle_results`, and `judge_status_of` in `judge_bundle.py`. Run this file and
      `python -m pytest -q tests/unit/test_benchmark_resilience.py tests/unit/test_benchmark_judge_identity.py tests/unit/test_benchmark_ragas_dialect.py tests/unit/test_ragas_scoring_characterization.py`,
      and watch all pass. Tick parent tasks 2.4 and 2.5. Gate, then commit (message:
      `write a bundle in place of the judge in a deferred arm`).

## 6. Exclusive link and publish (parent tasks 2.5a and 2.6)

- [ ] 6.1 Create `tests/unit/test_exclusive_link.py`. Tests (design D10): a new name
      gets the full bytes and `True`, and no `.tmp.*` file is left; a second writer of
      the same name gets `False`, and the first bytes stay; replace `os.link` in the
      module with a function that calls the real `os.link` and then raises
      `FileExistsError`, and the writer gets `True`; replace `os.link` with a function
      that asserts the temporary file already has the full bytes and that `os.fsync`
      was called on it, so the name never exists without its content; 8 writers with
      different bytes in a `ThreadPoolExecutor` (each `future.result(timeout=10)`, no
      barrier) give exactly one `True`, and the file holds that writer's bytes; the
      module imports nothing from `src` (read its source and check). Run the file and
      watch it FAIL. Then write `src/utils/exclusive_link.py`. Watch it pass. Tick
      parent task 2.5a. Gate, then commit (message: `add a no-replace link primitive`).

- [ ] 6.2 Create `tests/unit/test_judge_publish.py`, importing
      `from scripts.benchmarking.judge import publish`. Build bundles with
      `write_judge_bundle` in `tmp_path`, and a queue folder in `tmp_path`. Tests
      (design D11, parent task 2.6): `--queue` is required (argparse exits 2); a bundle
      with `BUNDLE_COMPLETE` becomes `READY` in the queue with equal files, and `READY`
      is created last, through `link_exclusive`; a bundle with no `BUNDLE_COMPLETE` is
      not copied; a corrupted copy (patch the copy step to change one byte) exits
      non-zero with no `READY`; a copy step that raises after `rows.jsonl` leaves no
      `READY`, and the next run completes it; a second run over a READY bundle with the
      same digest changes nothing (compare the tree and the mtimes) and exits 0; a READY
      queue folder with a different digest exits non-zero and changes nothing; the
      pending result JSON is not copied with no `RESULT_COMPLETE`, or when it does not
      load (write half a file), and is copied to `<queue>/results/` after both are
      true, only when each bundle's arm entry has `judge_status: pending` and the same
      digest; a wrong digest there exits non-zero; a result with `RESULT_COMPLETE` and
      `arm_count: 2` whose arm-1 bundle is missing is not copied, exits non-zero, and the
      message names arm 1; a judged file in
      `<queue>/results/` is copied back into the output folder only after every arm
      folder of that result has `MERGED`. Run the file and watch it FAIL. Then write
      `scripts/benchmarking/judge/publish.py`. `run_goldenset_eval.sh` does not call it.
      Watch it pass. Tick parent task 2.6, and leave 2.7 unticked. Gate, then commit
      (message: `add the judge bundle publish script`).

## 7. Correct the parent change

- [ ] 7.1 In `openspec/changes/offload-ragas-judge-to-slurm/design.md`, append one short
      paragraph to D3, as #730 did for D2. Say that PR 2 (#731) fixes the basename at
      the top of `run()` for every run; that the judge model id comes from
      `evaluator_model` and the revision from the new `evaluator_model_revision`; that
      `judge_bundle_dir` is relative to the output folder (default `judge_bundles`), and
      that all configs of a deferred run must resolve to the same one;
      that the arm index is 0-based; that the bundle digest is `rows_sha256`, recorded
      on the arm as `judge_bundle.digest`; that an arm with no scorable row still writes
      a bundle; that the start checks run in `Benchmarker.__init__`; that the `link`
      primitive is `src/utils/exclusive_link.py` and the package digest is
      `collect_package_digest` in `src/utils/benchmark_provenance.py`; and that
      `arm_divergence_refusal` is at `src/cli/tools/config_seed.py:130`. In
      `openspec/changes/offload-ragas-judge-to-slurm/specs/offline-judge-scoring/spec.md`,
      the THEN line of the scenario "Bundle names the result before the result exists"
      ends with the stray sentence "When `judge_mode` is absent or `inline`, the run
      SHALL score inline exactly as before." Move that sentence to the end of the
      requirement's first paragraph (keep the first line's SHALL on the first line). In
      `openspec/changes/offload-ragas-judge-to-slurm/proposal.md`, add
      `evaluator_model_revision` to the "Config" bullet's list of new optional keys, with
      the words "the judge revision that a deferred bundle asks for". Change nothing else
      in those three files. Run
      `openspec validate offload-ragas-judge-to-slurm --strict` and
      `openspec validate fix-issue-731-judge-deferred-bundle --strict`, and confirm both
      pass. Gate, then commit (message: `correct judge offload design d3 to the pr 2
      scope`).

## 8. Publish

- [ ] 8.1 Run `bash scripts/gate.sh` once more on the branch tip and confirm it exits 0.
      Confirm each acceptance criterion of issue #731 against the tree. Push the branch:
      `git push -u origin fix/issue-731-judge-deferred-bundle`. The PR already exists
      (PR #747, opened by the loop harness). Do NOT run `gh pr create`. Edit PR #747:
      `gh pr edit 747 --repo fasrc/archi --title "..." --body-file /tmp/pr747-body.md`
      with the title `feat(benchmark): judge_mode deferred and the judge bundle writer
      (judge offload PR 2)` and `Closes #731` in the BODY (not the title). Write the body
      file under `/tmp`; do NOT commit a PR-body file to the repository. Replace the
      whole body, but keep its "Review findings" section if it has one. In the body: list the acceptance criteria with their evidence (test
      names, the gate result); list the driver decisions a reviewer must check (design
      D2 `evaluator_model_revision`, D3 refusals, D4 basename for inline runs, D5
      manifest keys, D6 zero-row bundle, D8 `judge_bundle` key); say that task 2.7 and
      `publish.py --status` stay with the operator on #729; and say in one sentence that
      `openspec/changes/fix-issue-731-judge-deferred-bundle/` is a driver for the
      nightly executor and that `offload-ragas-judge-to-slurm` stays the design of
      record (issue #731 asked for no new change, and the branch name differs from the
      issue's `feat/judge-deferred-bundle` for the same reason). STOP at the open PR. Do
      not merge.
