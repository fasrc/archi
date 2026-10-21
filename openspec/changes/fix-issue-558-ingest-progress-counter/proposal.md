## Why

`/api/ingestion/status` returns the same payload for the whole `update_vectorstore()` phase
(about 106 minutes on the dev corpus). The payload has only `state`, `step` and `error`, and
`step` stays at `"Updating vectorstore"` for that whole phase. As a result, the benchmark
cannot tell a wedged ingest from a working one. Its stall budget
(`BENCH_INGEST_WAIT_TIMEOUT`) restarts on every `running` poll, so only the absolute
ceiling (`BENCH_INGEST_MAX_WAIT`, 6 h) can stop a wedged ingest.

This is child A of #428 and the only child that fixes the wedge case (issue #558).

## What Changes

- The ingestion status payload gets one new, optional key: `progress`, either `null` or
  `{"done": <int>, "total": <int or null>}`. It is `null` until the embedding loop starts,
  is reset to `null` at the start of each ingest, and advances after each committed batch
  of files in `VectorStoreManager._add_to_postgres`.
- A new keyword argument, `embedding_progress`, carries a `(done, total)` callback from
  `build_ingestion_helpers` through `DataManager.run_ingestion` and
  `VectorStoreManager.update_vectorstore` to the commit sites. Callers that do not pass it
  get today's behaviour.
- The benchmark's stall rule: when the payload carries a `progress` dict, a `running` poll
  restarts the stall budget only if `progress.done` differs from the last accepted value.
  When `progress` is absent, `null` or malformed (an older data manager), the rule is
  unchanged.
- `state`, `step` and `error` keep their names and meanings. `set_ingestion_status` keeps
  its signature and does not touch `progress`.
- Docs: `docs/docs/benchmarking.md` and `docs/docs/api_reference.md` describe the new key
  and the new stall rule.

## Capabilities

### New Capabilities
- `ingestion-progress`: the data manager publishes an embedding progress counter in the
  ingestion status payload, and the benchmark keys its stall budget off that counter.

### Modified Capabilities
(none)

## Impact

- `src/utils/ingestion_status.py` — new `progress` key and `set_ingestion_progress`.
- `src/data_manager/data_manager.py` — `run_ingestion(..., embedding_progress=None)`.
- `src/data_manager/vectorstore/manager.py` — `update_vectorstore`, `_sync_vectorstore`,
  `_add_to_postgres` take `embedding_progress=None`; the four commit sites report.
- `src/bin/service_benchmark.py` — `_ingest_is_progressing` and the wait loop.
- Tests: `tests/unit/test_ingestion_status_lock.py`,
  `tests/unit/test_vectorstore_manager_batch_commit.py`, `tests/unit/test_ingest_run.py`,
  `tests/unit/test_benchmark_ingest_wait.py`, and a data-manager test.
- Out of scope: `run_id` and timestamps (#559), background updates reported as running
  (#560), new budget defaults.
