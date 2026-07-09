## 1. Pure validation in `benchmark_schema.py` (TDD)

- [x] 1.1 Write failing tests in `tests/unit/test_benchmark_bank_preflight.py` for
  `validate_bank(bank, benchmarking_configs)`: (a) modern bank missing `sources` under
  `modes:[RAGAS,SOURCES]` → one error per item naming `['sources']`; (b) same bank under
  `modes:[RAGAS]` → no errors; (c) legacy-dialect bank (`question`/`answer`) normalizes and
  passes when it carries the required fields; (d) a non-dict item → error; (e) valid bank
  with `user_input`+`sources` → empty list.
- [x] 1.2 Add `validate_bank` to `src/utils/benchmark_schema.py` (normalize, then per-item
  presence check against `required_fields_for_modes`; return `List[str]`). Make 1.1 green.
- [x] 1.3 Write failing tests for `bank_eligibility_warnings`: under `modes:[RAGAS]` with
  `enabled_metrics` including `context_recall`, a bank where some rows have empty
  `reference` → a warning naming the metric and the `n_ok/total` denominator; a bank where
  all rows have `reference` → no warnings; `modes` without `RAGAS` → no warnings.
- [x] 1.4 Add `bank_eligibility_warnings` to `benchmark_schema.py` (reuse
  `metric_required_column`). Make 1.3 green.
- [x] 1.5 Write failing tests for `preflight_bank_file(queries_path, benchmarking_configs)`
  using tmp files: valid file → `([], warnings)`; bank missing `sources` → non-empty
  errors; missing file / non-JSON / non-list → a single hard error (no exception raised).
- [x] 1.6 Add `preflight_bank_file` (read JSON, `normalize_bank`, delegate). Make 1.5 green.
- [x] 1.7 Run pyright on `src/utils/benchmark_schema.py`; no new errors vs baseline.

## 2. CI drift-guard fixture

- [x] 2.1 Add `tests/unit/fixtures/canonical_bank_modern.json` — a minimal modern-dialect
  bank (`user_input`, `reference`, `sources`) representative of a valid post-#96 bank.
- [x] 2.2 Add a test asserting `validate_bank(canonical, {modes:[RAGAS,SOURCES]}) == []`,
  so a future change to `required_fields_for_modes` that outdates this shape fails CI.

## 3. Standalone validator script

- [x] 3.1 Write a failing test that imports `scripts/benchmarking/validate_queries.py`
  `main()` and runs it against a tmp config+bank: exit 0 on a valid bank, exit 1 with the
  missing-field message on an invalid one; plus a `-q` override case.
- [x] 3.2 Add `scripts/benchmarking/validate_queries.py`: argparse (`-c`, optional `-q`),
  load config YAML → `services.benchmarking`, call `preflight_bank_file`, print
  errors/warnings, return/exit 1 on errors. Make 3.1 green.

## 4. Fail-fast wiring in `archi evaluate`

- [x] 4.1 In `src/cli/cli_main.py::evaluate`, immediately after `benchmarking_configs =
  config_manager.get_interface_config("benchmarking")` (before `build_compose_config` /
  volume creation / `prepare_deployment_files`): call `preflight_bank_file` on
  `benchmarking_configs.get("queries_path")`; `logger.warning` each warning; on errors,
  `raise click.ClickException("Benchmark question bank failed preflight ...")`.
- [x] 4.2 Keep the call site thin (delegate all logic to the tested library). Verified:
  `archi evaluate` on the current `ragas-snow-pt1-clean.*` aborts in **1 s** with EXIT=1 and
  the `sources` error, creating **no** deploy dir and **no** volumes.

## 5. bench.sh precondition — SUPERSEDED

- [x] 5.1 Not needed: the built-in `archi evaluate` preflight (task 4) fails fast before any
  deploy for every invocation, so a separate `bench.sh` guard is redundant. `bench.sh` is
  also git-ignored local state and not part of this change.

## 6. Gate + verify

- [x] 6.1 `bash scripts/gate.sh` green: black/isort clean, 727 passed / 1 xfailed, diff-cover
  **88%** patch coverage vs origin/dev (`benchmark_schema.py` 100%; only the thin
  `cli_main.py` call site uncovered).
- [x] 6.2 Adversarial check: valid banks pass preflight (unit tests + canonical fixture);
  `preflight_bank_file` returns `[]` for a well-formed bank, so a real run is never
  false-blocked.
