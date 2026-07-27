## 1. Baseline the current state

- [x] 1.1 `export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH` and run `python -m pytest tests/unit/test_goldenset_maintenance.py tests/unit/test_goldenset_maintenance_script.py -q` — confirm the suite is green before changing anything (record the count).

## 2. RED — find_drift baselines a draft row

- [x] 2.1 In `tests/unit/test_goldenset_maintenance.py`, add a failing test: `find_drift(..., baseline_drafts=True)` fetches and hashes a `draft` row's sources and exposes a fresh `source_hashes` digest for that row on the report (as a baseline-only result, not in `report.rows`).
- [x] 2.2 Run the test and watch it fail for the right reason (the parameter/field does not exist yet).

## 3. RED — the safety invariant

- [x] 3.1 In `tests/unit/test_goldenset_maintenance.py`, add failing tests asserting that a `draft` row baselined via `baseline_drafts=True` does NOT appear in `report.drifted`, does NOT appear in `report.unbaselined`, does NOT trigger an LLM call (assert the judge/LLM is not invoked for it), does NOT count toward `checked_rows`, and does NOT change the abstention decision.
- [x] 3.2 Run and watch these fail for the right reason.

## 4. GREEN — implement in find_drift

- [x] 4.1 Add a `baseline_drafts: bool = False` parameter to `find_drift` in `src/utils/goldenset_maintenance.py`; when off, behavior is byte-for-byte unchanged (the `row_status(record) != "locked"` gate at ~:1572 still skips drafts for drift).
- [x] 4.2 When `baseline_drafts=True`, fetch + hash `draft` rows' sources and collect them into a dedicated field on `DriftReport` (e.g. `baseline_only`), kept out of `rows`/`drifted`/`unbaselined` so the aggregates (`checked_rows`, abstention, LLM calls) are structurally unaffected.
- [x] 4.3 Run the tests from groups 2 and 3 until green.

## 5. RED+GREEN — CLI flag and read-only guarantee

- [x] 5.1 In `tests/unit/test_goldenset_maintenance_script.py`, add a failing test: a CLI flag (`--baseline-drafts`, usable with `--print-hashes`) emits a `source_hashes` block for a `draft` row.
- [x] 5.2 In the same file, add a failing test asserting the bank file is byte-identical before and after a `--print-hashes` run that includes drafts.
- [x] 5.3 Implement the `--baseline-drafts` flag in `scripts/benchmarking/goldenset_maintenance.py`, plumb it into `find_drift`, and make `_print_hashes` (~:855) also emit blocks for `report.baseline_only`. Run until green.

## 6. Replace the unsafe guidance

- [ ] 6.1 Replace the "set `status: locked` on the row, then re-run this to get its block" message at `scripts/benchmarking/goldenset_maintenance.py:898-903` with guidance pointing at `--baseline-drafts` and the single-edit workflow.
- [ ] 6.2 Confirm `grep -n 'set .status: locked. on the row, then re-run' scripts/benchmarking/goldenset_maintenance.py` returns nothing.

## 7. Docs

- [ ] 7.1 Update the "Recording a baseline" section of `docs/docs/benchmarking.md` so the documented workflow is a single edit (compute the hash with `--baseline-drafts --print-hashes`, then paste `status: locked` and `source_hashes` together).
- [ ] 7.2 Audit every `drift` example in `docs/docs/benchmarking.md` for the required `--allowed-hosts` flag; confirm the two grep counts in the issue's Commands block are equal.
- [ ] 7.3 `cd docs && mkdocs build --strict` exits 0.

## 8. Gate and verify

- [ ] 8.1 `black src/ scripts/ tests/ -q && isort src/ scripts/ tests/ -q`.
- [ ] 8.2 `python -m pytest tests/unit/ -q` passes; total count > 1290.
- [ ] 8.3 `openspec validate fix-issue-146-baseline-drafts --strict` passes.
- [ ] 8.4 Commit only green through the pre-commit gate (≥80% diff coverage; never `--no-verify`); push and open a PR to `fasrc/archi:dev` with `closes #146`.
