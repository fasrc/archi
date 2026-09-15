## Why

Recording a golden-set baseline today is a two-step edit: mark a row `status: locked`, then run `drift --print-hashes` to get its `source_hashes` block and paste it in. The `drift` command only fetches and hashes rows that are *already* `locked`, so between the two steps the row is authoritative but unbaselined — and a crash, a forgotten re-run, or an interrupted session leaves it that way until a human repairs it by hand. Every row in the live bank (105 rows) is currently `draft`, so confirming the bank means 105 passes through that unsafe window. Making the hash available *before* locking collapses the two-step into a single edit.

## What Changes

- Add an opt-in mode to the `drift` detector that fetches and hashes a `draft` row's sources so its `source_hashes` block can be printed *before* the row is locked.
- Surface these baseline-only rows through a dedicated field on the drift report (kept separate from drift findings), so the safety invariant — *draft rows are never drift-checked* — is structural rather than a set of conditionals a later change could drift from.
- Add a CLI flag (e.g. `--baseline-drafts`, usable with `--print-hashes`) that emits a paste-ready `source_hashes` block for `draft` rows. The tool still **never writes the bank file**.
- Replace the CLI guidance that currently teaches the unsafe "lock first, then re-run" order with guidance pointing at the new flag (single-edit workflow).
- Update `docs/docs/benchmarking.md` "Recording a baseline" section to document the single-edit workflow, and ensure every documented `drift` example carries the required `--allowed-hosts` flag.

## Capabilities

### New Capabilities
- `goldenset-baseline-drafts`: Compute and print a `draft` golden-set row's `source_hashes` baseline ahead of locking, via the existing read-only `--print-hashes` path, without the tool ever writing the bank and without subjecting draft rows to drift detection.

### Modified Capabilities
<!-- None. The drift capability lives in the in-flight (unarchived) change `maintain-ragas-goldenset`, so there is no archived spec in openspec/specs/ to delta against; this change adds a self-contained capability that composes with it. -->

## Impact

- `src/utils/goldenset_maintenance.py` — `find_drift` (the `row_status(record) != "locked"` gate at ~:1572) and the `DriftReport` shape (new baseline-only field).
- `scripts/benchmarking/goldenset_maintenance.py` — `_print_hashes` (~:855) and the "lock first" guidance message (~:898-903); new CLI flag.
- `docs/docs/benchmarking.md` — "Recording a baseline" section and every `drift` example's `--allowed-hosts` flag.
- Tests: `tests/unit/test_goldenset_maintenance.py`, `tests/unit/test_goldenset_maintenance_script.py`.
- No change to fetch behavior, LLM usage, or the bank file (read-only tool guarantee preserved). No new dependencies.
