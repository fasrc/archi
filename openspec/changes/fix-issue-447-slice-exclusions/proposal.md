# Report the re-labelling count even when no slice row survives (issue #447)

## Why

`scripts/benchmarking/compare_runs.py` counts the questions whose bank label differs between
arms (`excluded_mismatched`) so that a bank edit is visible, not silent. Two gaps make that
count disappear:

- **Gap A — the count has no carrier.** `slice_block` (`compare_runs.py:1748`) attaches the
  count only to an emitted slice row (`:1872`). When every question is re-labelled, or every
  surviving group scores `n == 0`, no row is emitted and the count is lost. The renderer
  (`:2390-2427`) then prints "No slice field (…) is present in every arm.", which is false.
- **Gap B — the baseline's label gates the count.** The membership loop reads the baseline's
  value first and skips the question when it is not a non-empty string (`:1801-1803`). A
  disagreement between two other clean arms then goes uncounted, so the count moves with
  `--baseline` when the baseline's row carries no label.

The operator resolved both design choices on 2026-09-26 (issue body, "Decision").

## What Changes

- New top-level report key `report["slice_exclusions"] = {field: n}`, filled for every slice
  field that passes the `has_metric` gate, zero included, whether or not a slice row exists.
  It is in the `--json` output.
- The disagreement check runs over the **labelled** clean arms only. A clean row with no label
  (field absent, `null`, or `""`) is skipped, the baseline included. A question whose baseline
  row has no string label is still dropped from every group, because the label is the group key.
- `excluded_mismatched` stays on each emitted slice row, with the same value as
  `slice_exclusions[field]`, for backward compatibility.
- The renderer builds the "N question(s) carry a different …" sentence from
  `slice_exclusions`. It prints "No slice field (…) is present in every arm." only when the
  `has_metric` gate excluded every field.
- `slice_block`'s docstring and `docs/docs/interpreting_benchmark_results.md` ("When the tool
  says a row was re-labelled") drop the "#447 limits" text and describe the new contract.

## Out of scope

- Slice pairing rules (G5/G6) and significance verdicts.
- How labels are recorded in artifacts (#431).

## Impact

- Code: `scripts/benchmarking/compare_runs.py` only.
- Tests: `tests/unit/test_compare_runs.py`.
- Docs: `docs/docs/interpreting_benchmark_results.md`.
- `--json` consumers gain one key; no key is removed or renamed.
