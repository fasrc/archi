## Why

Issue #521 lists four small defects in the benchmark judge-knob validator and the
report writer. An oversized integer `timeout` (`10**309`) makes
`_positive_number` raise `OverflowError` instead of falling back. The
`selected_file_digest` hashes the configuration after `map_prompts` replaced each
prompt path with the file contents, so it does not fingerprint the file as written.
A `timeout` of `600` and one of `600.0` give different effective digests for the
same run. And `docs/docs/benchmarking.md` does not document the `timeout`
fallback-and-warn contract.

## What Changes

- `_positive_number` (`src/utils/benchmark_schema.py`) treats a value that cannot
  convert to a float as invalid: it falls back to the default with the existing
  warning, and does not raise.
- `_positive_number` returns `int(value)` for an integral float, so `600` and
  `600.0` give equal effective settings and an equal effective digest. A
  non-integral float (`120.5`) stays a float.
- `ResultHandler.handle_results` (`src/bin/service_benchmark.py`) keeps an
  untouched deep copy of the parsed selected file and passes it as `selected=` to
  `config_version`. `selected_file_digest` and the divergence list then describe
  the file as written. A config without `prompts` keeps a byte-identical
  `selected_file_digest`.
- `docs/docs/benchmarking.md` documents the `timeout` contract and states that
  `configuration` holds the file with prompt paths inlined, while
  `selected_file_digest` fingerprints the file as written.

## Capabilities

### New Capabilities

- `benchmark-run-provenance`: requirements for the judge-knob validator and the
  selected-file fingerprint on a benchmark run record. (The capability is not yet
  in `openspec/specs/`, so this change uses ADDED requirements.)

### Modified Capabilities

None.

## Impact

- Code: `src/utils/benchmark_schema.py` (`_positive_number`),
  `src/bin/service_benchmark.py` (`ResultHandler.handle_results`).
- Tests: `tests/unit/test_benchmark_schema.py`,
  `tests/unit/test_benchmark_report_records_running_config.py`.
- Docs: `docs/docs/benchmarking.md`.
- Out of scope: `_positive_int` and `max_workers`, `benchmark_provenance.py` logic,
  `deploy/**`, `config/**`, the templates.
- No change for template-rendered configs: `src/cli/templates/base-config.yaml`
  never renders `services.benchmarking.prompts`.
