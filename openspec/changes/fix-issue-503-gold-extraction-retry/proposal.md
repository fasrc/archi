## Why

In `archi eval qa`, a gold-atom extraction that the provider returns in the wrong shape (for
example a non-list `atoms`, although `GOLD_ATOM_SCHEMA` declares `atoms: array`) fails the item
as `preparation_failed` with no retry. The run still exits 0 with 108 of 109 items, and no
summary or report line shows that an item is missing. The pass rate only shrinks its
denominator, so the loss is silent (#503, defect 16 of #396).

## What Changes

- `prepare_dataset_item` retries `extractor.extract_gold` **once** when
  `validate_gold_output` raises `ValueError` (a shape violation). It does not retry on
  `OracleResolutionError`, on an exception from `extract_gold` itself, or on any other
  exception. A second shape violation fails the item as today.
- A preparation record whose extraction was retried carries `gold_extraction_attempts: 2`
  (prepared or `preparation_failed`). The row reader accepts this optional key. Records with
  no retry are byte-identical to today.
- The usage on a retried record is the sum of both extractor calls (`sum_usage`), so the
  retry's tokens are not lost.
- `build_summary` takes `items_total` (the manifest's `phases.prepare.input_items`) and
  `summary.json` gains `items_total` and `scored_items` (items with at least one `scored`
  attempt). Both workflow callers pass `items_total`.
- `report.md`'s header gains one line, `- Scored items: <scored_items> / items: <items_total>`.
- Every existing key, status, and CLI exit code stays the same.

## Out of scope

- CLI exit-code semantics in `src/cli/qa_eval.py` (the fail-closed wrapper guard is #434).
- The `retry` phase in `workflow.py` (it re-runs attempts, not items).
- The `overall_attempt_pass_rate` denominator.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `qa-evaluation-trial`: ADDED requirements for the one-time gold-extraction retry and for
  the `scored / items` coverage line in `summary.json` and `report.md`.

## Impact

- `src/evaluation/qa/preparation.py` — retry, record field, row reader.
- `src/evaluation/qa/scoring.py` — `items_total`, `scored_items`, report header line.
- `src/evaluation/qa/workflow.py` — two `build_summary` call sites pass `items_total`.
- Tests: `tests/unit/evaluation/qa/test_preparation.py`, `test_scoring.py`,
  `test_workflow.py`.
