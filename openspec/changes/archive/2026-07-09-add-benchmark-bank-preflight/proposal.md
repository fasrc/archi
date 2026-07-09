## Why

A benchmark run (`archi evaluate`) validates the question bank **per-question at grading
time** — which is *after* the data-manager re-ingest (~50 min). When the bank does not
match the schema the harness requires for the configured modes, every question is silently
skipped and the operator only finds out at the very end, with all-`nan` aggregates and an
empty `single_question_results`.

This actually happened: post-#96 the harness requires `sources` per item whenever `SOURCES`
mode is enabled (`required_fields_for_modes`), but the git-ignored local bank
`ragas-snow-pt1-clean.json` (modern ragas dialect: `user_input` / `retrieved_contexts` /
`response` / `reference`) carries no `sources`. All 27 items failed validation → 0 scored →
~50 minutes of ingest wasted. Because the banks are git-ignored local state, nothing in CI
guards this drift, and because the check runs after ingest, nothing fails fast.

The harness already *knows* the required fields at config-load time. The fix is to run that
same schema check **before** the deploy/ingest, and to expose it as a standalone tool so a
bank can be validated in under a second while it is being authored.

## What Changes

- **Reusable, pure validation** in `src/utils/benchmark_schema.py` (the harness's existing
  single-source-of-truth for the dialect/schema contract): a `validate_bank(bank,
  benchmarking_configs)` that returns human-readable schema errors (empty = valid), and a
  `bank_eligibility_warnings(bank, benchmarking_configs)` that reports per-metric
  eligibility (e.g. context metrics that will score on a subset because some rows have an
  empty `reference`). Both reuse `normalize_bank` / `required_fields_for_modes` /
  `metric_required_column`, so they can never drift from what the benchmarker enforces.
- **A file-level preflight** `preflight_bank_file(queries_path, benchmarking_configs)` that
  loads + normalizes the bank and returns `(errors, warnings)`.
- **Fail-fast in `archi evaluate`** (`src/cli/cli_main.py`): immediately after the
  benchmarking config is resolved and before any volume/compose/ingest work, run the
  preflight; on errors, abort with a `ClickException` listing the offending items; on
  warnings, log them and continue.
- **A standalone validator** `scripts/benchmarking/validate_queries.py` (thin CLI over the
  library) for pre-run checks without deploying, plus a precondition line in `bench.sh`.
- **CI unit tests** covering `validate_bank` / `bank_eligibility_warnings` /
  `preflight_bank_file` and a committed canonical modern-dialect fixture that must pass —
  so a future harness schema change that outdates the bank format is caught in CI.

## Capabilities

### New Capabilities
- `benchmark-bank-preflight`: Validate a benchmark question bank against the harness's
  required schema for the configured modes **before** deploy/ingest, failing fast with
  per-item errors and emitting per-metric eligibility warnings. Exposed as a library
  function, a standalone CLI, and an automatic preflight inside `archi evaluate`.

### Modified Capabilities

None. Existing benchmark specs are unaffected; this adds a guard in front of the run.

## Impact

- **Code**: `src/utils/benchmark_schema.py` (new pure functions `validate_bank`,
  `bank_eligibility_warnings`, `preflight_bank_file`); `src/cli/cli_main.py` (thin
  fail-fast call site in `evaluate`); `scripts/benchmarking/validate_queries.py` (new
  standalone CLI); `bench.sh` (precondition line).
- **Tests**: `tests/unit/test_benchmark_bank_preflight.py` (validation + eligibility +
  file preflight), `tests/unit/fixtures/` canonical bank(s). No changes to existing tests.
- **Behavior**: Backward-compatible. A valid bank runs exactly as before. An invalid bank
  now aborts `archi evaluate` in seconds with a clear message instead of wasting the
  ingest. Warnings never block a run.
- **Not in scope**: migrating the git-ignored banks themselves to carry `sources`;
  changing `SOURCES`-mode scoring; moving the per-question validation currently in
  `service_benchmark._process_config` (it stays as the second-line guard).
