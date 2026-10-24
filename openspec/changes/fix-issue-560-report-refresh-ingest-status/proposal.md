## Why

`/api/ingestion/status` reports only the initial ingest. A scheduled source refresh
(`run_locked` in `src/bin/service_data_manager.py:71-80`) and an upload-triggered
`update_vectorstore(force=True)` (`trigger_update`, `:82-84`) change the corpus but never
touch the status dict. During those runs the endpoint keeps the old `completed`, so a
benchmark that waits on the endpoint starts against a corpus that is being changed. The
benchmark's own docstring names this gap (`src/bin/service_benchmark.py`
`_ingest_is_progressing`: "neither of which touches this status dict").

This is child C of #428 (issue #560). Siblings: #558 (progress counter, merged) and #559
(`run_id` and timestamps).

## What Changes

- `build_ingestion_helpers` returns two new helpers:
  - `run_tracked(step, fn)` takes the ingestion lock, publishes `running` with the given
    `step` (and `progress` reset to `null`), runs `fn`, then publishes `completed`. If
    `fn` raises, it publishes `error` with the message and re-raises.
  - `run_source_refresh(name, func, update_vectorstore, set_source_status)` holds the
    body that `run_locked` has today (per-source status `running`, `func()`,
    `update_vectorstore(force=True)`, per-source status `idle` with `last_run`) and runs it
    through `run_tracked(f"scheduled:{name}", ...)`.
- `run_locked` and `trigger_update` in `service_data_manager.py` become one-line call
  sites: `run_source_refresh(...)` and `run_tracked("upload", ...)`.
- `_ing = build_ingestion_helpers(...)` moves above `run_locked`, so the helpers exist
  before the scheduler starts.
- **Accepted policy change (from the issue):** a benchmark that waits on the status
  endpoint now waits during a routine refresh. That is correct: the corpus is changing.
- Additive: `state`, `step`, `error`, `progress` keep their meanings. The trigger name is
  in `step`.

## Capabilities

### New Capabilities
- `ingestion-refresh-status`: scheduled source refreshes and upload-triggered vectorstore
  updates publish a `running` → `completed` or `error` lifecycle in the ingestion status
  payload.

### Modified Capabilities
(none)

## Impact

- `src/utils/ingestion_status.py` — `run_tracked`, `run_source_refresh`.
- `src/bin/service_data_manager.py` — thin call sites only (about 5 changed lines).
- `docs/docs/api_reference.md` — `GET /api/ingestion/status` describes the new `step`
  values.
- Tests: `tests/unit/test_ingestion_status_lock.py`.
- Out of scope: the progress counter (#558), `run_id` and timestamps (#559), the benchmark
  wait loop, the per-source status file behavior.
