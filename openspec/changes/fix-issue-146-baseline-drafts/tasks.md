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

- [x] 6.1 Replace the "set `status: locked` on the row, then re-run this to get its block" message at `scripts/benchmarking/goldenset_maintenance.py:898-903` with guidance pointing at `--baseline-drafts` and the single-edit workflow.
- [x] 6.2 Confirm `grep -n 'set .status: locked. on the row, then re-run' scripts/benchmarking/goldenset_maintenance.py` returns nothing.

## 7. Docs

- [x] 7.1 Update the "Recording a baseline" section of `docs/docs/benchmarking.md` so the documented workflow is a single edit (compute the hash with `--baseline-drafts --print-hashes`, then paste `status: locked` and `source_hashes` together).
- [x] 7.2 Audit every `drift` example in `docs/docs/benchmarking.md` for the required `--allowed-hosts` flag; confirm the two grep counts in the issue's Commands block are equal.
- [x] 7.3 `cd docs && mkdocs build --strict` exits 0.

## 8. Gate and verify

- [x] 8.1 `black src/ scripts/ tests/ -q && isort src/ scripts/ tests/ -q`.
- [x] 8.2 `python -m pytest tests/unit/ -q` passes; total count > 1290.
- [x] 8.3 `openspec validate fix-issue-146-baseline-drafts --strict` passes.
- [x] 8.4 Commit only green through the pre-commit gate (≥80% diff coverage; never `--no-verify`); push and open a PR to `fasrc/archi:dev` with `closes #146`.

## 9. Review round 1 — three P2 findings from the PR review

- [x] 9.1 RED+GREEN: a baselined draft is still counted in `skipped_rows`. The draft branch reached `continue` without incrementing `skipped`, so enabling the flag moved a detection metric — a one-draft bank went from `1 skipped` to `0` while still skipping the row, and the CLI's locked-row summary stopped accounting for it. Assert the count is identical with the flag off and on.
- [x] 9.2 RED+GREEN: `BaselineRow.missing` names every source that produced no hash (unreachable, refused by the allowlist, unparseable URL). `_fetch_extract`'s error was discarded, so a multi-source draft with one failed fetch emitted a paste-ready block missing that source with no warning — and unlike a locked row, a draft has no stored hash to carry forward. Include a test that a fully-hashed draft reports `missing == ()`, so the other assertions cannot pass against an always-populated field.
- [x] 9.3 RED+GREEN: `_print_hashes` labels an incomplete draft block `INCOMPLETE` and names the missing sources, mirroring the locked-row path; a draft whose every source failed is listed and named rather than skipped silently.
- [x] 9.4 RED+GREEN: `main` rejects `--baseline-drafts` without `--print-hashes` with exit `2`, **before any fetch**. Assert zero fetch calls, not merely the exit code — the cost being avoided is one request per draft source, so a rejection that fired after fetching would pass an exit-code-only test while fixing nothing.
- [x] 9.5 Mutation-check each of the four fixes: reverting it must fail the specific test that owns it (`skipped += 1`; the failed-fetch `missing` append; the `INCOMPLETE` print; the flag rejection). Confirmed — each mutant killed by its owning test, and restored green afterwards.
- [x] 9.6 Update `docs/docs/benchmarking.md`: the `--print-hashes` requirement and its rationale, and the draft-specific completeness rule (a draft has no stored hash to carry forward, so locking on an `INCOMPLETE` block leaves the source unbaselined).
- [x] 9.7 `openspec validate fix-issue-146-baseline-drafts --strict` passes; `cd docs && mkdocs build --strict` exits 0; full gate green through the pre-commit hook.
