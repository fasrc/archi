## Context

Anchors re-verified on `origin/dev` `6b8afe98` on 2026-10-24 (`src/evaluation/qa/`). Task 1.1
merged with PR #620, so the record field and the row reader already exist on `dev`:

- `preparation.py:291` `prepare_dataset_item`. The extractor call and validation are one
  expression at `:337-347`: `validate_gold_output(extractor.extract_gold(...), context=...)`.
  The bare `except Exception` at `:350` writes `preparation_failed`.
- `preparation.py:335-336` sets `_extractor_was_called` / `_usage_recorded`; `:349` and `:352`
  read `extractor.last_usage` (the runtime resets it on every call, `runtime.py:248`).
- `validation.py:45` `validate_gold_output` raises `ValueError` on a shape violation.
- `preparation.py:419` `_record_from_row` validates rows with `_require_exact_keys` (`:405`).
  An unknown key raises. The score phase re-reads `preparation.jsonl` through this reader
  (`workflow.py:869-872`). Task 1.1 already accepts `gold_extraction_attempts` (`:460-465`).
- `preparation.py:72` `PreparationRecord` has `gold_extraction_attempts` (`:91`); `to_dict`
  at `:234` emits it only when it is not `None` (`:269-270`, `:279-280`).
- `src/utils/llm_usage.py:126` `sum_usage` merges usage snapshots and skips `None`.
- `scoring.py:37` `build_summary`; the per-item dict sets `scored_attempts` at `:176`; return
  dict at `:209`. `_report_header` at `:272`; the overall pass-rate line at `:284-285`.
- `workflow.py:868` (score phase) and `workflow.py:1226` (retry phase) call `build_summary`.
  The manifest's `phases.prepare.input_items` is set at `workflow.py:279`. The retry phase
  copies the parent's `prepare` block into its own `manifest` (`:1043`), so both callers can
  read `manifest["phases"]["prepare"]["input_items"]`.

## Decisions

### D1 — What is retried
Split the expression: call `extract_gold` first, then call `validate_gold_output` in an inner
`try`. Only a `ValueError` raised **by `validate_gold_output`** triggers the retry. An
exception from `extract_gold` (any type, `ValueError` included) and `OracleResolutionError`
propagate to the existing `except Exception` unchanged, with no retry. The retry happens
once: the second call's validation result is final. Log one `WARNING` before the retry that
names the item id and the validation message (no answer text).

### D2 — Recording the retry
Add `gold_extraction_attempts: Optional[int] = None` to `PreparationRecord`. Set it to `2`
when a retry happened, on both the prepared record and the `preparation_failed` record. Leave
it `None` otherwise. `to_dict` emits the key only when it is not `None`, so a record with no
retry serialises exactly as today and every existing exact-row test stays green.
`__post_init__` rejects a value that is not an `int` (a `bool` is rejected) or is below 1.

### D3 — Reading the record back
In `_record_from_row`, when the status is `prepared` or `preparation_failed` and the row has
`gold_extraction_attempts`, add the key to the allowed fields and pass it to the record.
A row without the key stays valid (old runs re-score unchanged).

### D4 — Usage of a retried extraction
After the first call, keep `extractor.last_usage` as `first_usage`. After the retry, the
record's usage is `sum_usage([first_usage, extractor.last_usage])`. If both are `None` the
usage is `None`, as today. This applies to the prepared record and to the
`preparation_failed` record (the `except` path reads `last_usage` — it must use the merged
value when a retry happened).

### D5 — `items_total` and `scored_items`
`build_summary` gains a keyword-only `items_total: Optional[int] = None`. The summary dict
gains `items_total` (the value passed in) and `scored_items` (the count of items whose
`scored_attempts` is at least 1, counted in the existing per-item loop). Both workflow callers
pass `items_total=manifest["phases"]["prepare"]["input_items"]`. Existing keys are unchanged.

### D6 — Report header line
`_report_header` adds one line after the overall pass-rate line:
`- Scored items: \`<scored_items>\` / items: \`<items_total>\``. It reads both keys with
`summary.get(...)` and prints `unavailable` for a missing value, so a summary built before
this change still renders.

## Risks

- A provider that always violates the schema doubles its extraction cost for those items.
  The retry is bounded at one, and D4 records the extra tokens.
- `scored_items` counts items, not attempts. An item with 3 attempts and 1 scored counts as
  scored. The attempt counts stay in `attempt_lifecycle_counts`.
