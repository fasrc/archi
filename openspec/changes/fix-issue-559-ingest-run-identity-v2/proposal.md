## Why

`/api/ingestion/status` reports only `state`, `step`, `error`, and `progress`. It has no run identity and no timestamps. As a result, the benchmark can only approximate the ingest cost from the span it observed while it polled, and it records `ingest_wall_seconds: null` when an ingest ran before its first poll. The docstring of `wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2798`) names this gap and points to #428.

This change implements [#559](https://github.com/fasrc/archi/issues/559) (child B of #428). Siblings: #558 (progress counter and stall budget) and #560 (background updates reported as running) are merged, and #560 restructured `src/utils/ingestion_status.py`. This change is the operator's 2026-10-19 re-plan: the spec commit `27c6c7f8` from the old branch `fix/issue-559-ingest-run-identity`, moved to this folder name and updated to the run-boundary rule of the current module. No code from the old branch is reused.

## What Changes

- **Run identity and timestamps in the status payload (additive).** `build_ingestion_helpers` (`src/utils/ingestion_status.py:17`) adds three keys to the status dict, all `None` at first: `run_id` (a new UUID4 string for each visible run), `started_at`, and `finished_at` (ISO-8601 UTC strings). `state`, `step`, `error`, and `progress` keep their names and meanings.
- **The run boundary follows the published state.** Since #560, several tracked runs (the initial ingest, a scheduled source refresh, an upload) can share one visible `running` span. A visible run starts when the state **becomes** `running` and ends at the next terminal publish. `_set_running` sets a new `run_id` and `started_at` and clears `finished_at` only when the previous state was not `running`. `_finish` sets `finished_at` where it publishes `completed` or `error`. Both updates happen under the same `_status_lock` hold as the state change.
- **The benchmark uses the timestamps.** `wait_for_ingestion_completion` computes the ingest duration from `finished_at - started_at` of the `completed` payload when both parse and the wait saw no refresh or upload step. Otherwise it falls back to the harness-observed span of today.
- **Missed-ingest signal.** When the wait never sees the ingest progress, but the `run_id` at the `completed` poll differs from the `run_id` at the first answered poll, or the first answered poll is `completed` with a `finished_at` later than the harness start, the benchmark records that an ingest ran and the wait missed it.
- **Source field.** The return value of `wait_for_ingestion_completion` stays `Optional[float]`. The method also records `self.ingest_wall_source` (`"status_timestamps"`, `"harness_observed"`, `"missed"`, or `None`). `run()` passes it to `ResultHandler.handle_results`, which writes `ingest_wall_source` next to `ingest_wall_seconds` in each arm record.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: the ingestion status payload carries a run identity and start and finish timestamps for each visible run.
- `retrieval-benchmarking`: the benchmark derives the ingest duration from the status timestamps and records its source, including a missed ingest.

## Impact

- Code: `src/utils/ingestion_status.py` (`build_ingestion_helpers`, `_set_running`, `_finish`), `src/bin/service_benchmark.py` (`wait_for_ingestion_completion`, `run`, `ResultHandler.handle_results`).
- Tests: `tests/unit/test_ingestion_status_lock.py`, `tests/unit/test_benchmark_ingest_wait.py`, `tests/unit/test_benchmark_report_records_running_config.py`.
- API: `/api/ingestion/status` gains three optional keys. Readers that ignore unknown keys are not affected.
- Artifacts: each benchmark arm record gains `ingest_wall_source`. Older artifacts do not have the key; a reader treats an absent key as "artifact predates the field".
- Out of scope: the progress counter and the stall budget (#558); the scheduled and upload-triggered status reports (#560); report rendering of the new key.
