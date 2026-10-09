## Why

`/api/ingestion/status` reports only `state`, `step`, `error`, and `progress`. It has no run identity and no timestamps. As a result, the benchmark can only approximate the ingest cost from the span it observed while it polled, and it records `ingest_wall_seconds: null` when an ingest ran before its first poll. The docstring of `wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2614`) names this gap and points to #428.

This change implements [#559](https://github.com/fasrc/archi/issues/559) (child B of #428). Siblings: #558 (progress counter and stall budget), #560 (background updates reported as running).

## What Changes

- **Run identity and timestamps in the status payload (additive).** `build_ingestion_helpers` (`src/utils/ingestion_status.py`) adds three keys to the status dict, all `None` at first: `run_id` (a new UUID4 string for each run), `started_at`, and `finished_at` (ISO-8601 UTC strings). `run_initial_ingestion_async` sets `run_id` and `started_at` and clears `finished_at` when a run starts. It sets `finished_at` when the run ends with `completed` or `error`. `state`, `step`, `error`, and `progress` keep their names and meanings.
- **The benchmark uses the timestamps.** `wait_for_ingestion_completion` computes the ingest duration from `finished_at - started_at` of the `completed` payload when both parse. Otherwise it falls back to the harness-observed span of today.
- **Missed-ingest signal.** When the wait never sees the ingest progress, but the `run_id` at the `completed` poll differs from the `run_id` at the first answered poll, or the first `completed` payload has a `finished_at` later than the harness start, the benchmark records that an ingest ran and the wait missed it.
- **Source field.** The return value of `wait_for_ingestion_completion` stays `Optional[float]`. The method also records `self.ingest_wall_source` (`"status_timestamps"`, `"harness_observed"`, `"missed"`, or `None`). `run()` passes it to `ResultHandler.handle_results`, which writes `ingest_wall_source` next to `ingest_wall_seconds` in each arm record.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: the ingestion status payload carries a run identity and start and finish timestamps.
- `retrieval-benchmarking`: the benchmark derives the ingest duration from the status timestamps and records its source, including a missed ingest.

## Impact

- Code: `src/utils/ingestion_status.py`, `src/bin/service_benchmark.py` (`wait_for_ingestion_completion`, `run`, `ResultHandler.handle_results`).
- Tests: `tests/unit/test_ingestion_status_lock.py`, `tests/unit/test_benchmark_ingest_wait.py`, and the existing `handle_results` record tests.
- API: `/api/ingestion/status` gains three optional keys. Readers that ignore unknown keys are not affected.
- Artifacts: each benchmark arm record gains `ingest_wall_source`. Older artifacts do not have the key; a reader treats an absent key as "artifact predates the field".
- Out of scope: the progress counter and the stall budget (#558); scheduled and upload-triggered updates (#560).
