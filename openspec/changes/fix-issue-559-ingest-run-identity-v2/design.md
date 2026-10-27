## Context

`build_ingestion_helpers` (`src/utils/ingestion_status.py:17`) owns the status dict (`:34-39`) that `/api/ingestion/status` serves (`src/bin/service_data_manager.py:201-207`). Since #560, four entry points share it: `run_initial_ingestion_async` (`:142`), `run_tracked` (`:90`), `run_source_refresh` (`:115`), and `run_upload_update` (`:112`). Each one counts itself in with `_join` (`:65-76`), publishes its step with `_publish_running` (`:78-80`) when it holds `ingestion_lock`, and counts itself out with `_finish` (`:82-88`). Both `_join` and `_publish_running` call `_set_running` (`:60-63`) with `_status_lock` held. `_finish` publishes `completed` only when no run is queued (`_inflight == 0`), and publishes `error` at once. So several tracked runs can share one visible `running` span. During the initial ingest, the `progress_callback` calls `set_ingestion_status("running", step=...)` (`:45`) many times.

`Benchmarker.wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2798`) polls the endpoint and returns the harness-observed span, or `None` when it never saw the ingest progress. It does not count a refresh or upload step (`_is_refresh_step`, `:1620-1622`) as ingest cost: such a step before the ingest is ignored, and such a step after the ingest ends the span (`ingest_ended_at`). `run()` (`:2523`) passes the value to `ResultHandler.handle_results` (`:646`), which writes `ingest_wall_seconds` into each arm record (`:800`).

This design is the operator's 2026-10-19 re-plan of the 2026-10-09 design. That design set the run identity inside `run_initial_ingestion_async`, which predates #560.

## Goals / Non-Goals

**Goals:** an exact ingest duration when the data manager publishes timestamps; a distinct, recorded reading for "an ingest ran and the wait missed it"; additive payload and artifact changes only.

**Non-Goals:** the progress counter and stall budget (#558); the status reports of background updates (#560); any change to the stall or ceiling budgets of the wait; showing the new key in the Markdown or HTML reports.

## Decisions

### D1. A visible run starts when the state becomes running and ends at the next terminal publish

The status dict gets `run_id`, `started_at`, and `finished_at`, all `None` at first.

- `_set_running` reads the previous state before it updates the dict. When the previous state was **not** `running`, it also sets `run_id = str(uuid.uuid4())`, `started_at = now().isoformat()`, and `finished_at = None`. When the previous state was `running`, it keeps all three. This covers both callers: `_join` (the move from `pending`, `completed`, or `error`) and `_publish_running` (a queued run that gets the lock after an earlier run published `error`).
- `_finish` sets `finished_at = now().isoformat()` in the same `_status.update` that publishes `completed` or `error`. When `_finish` does not publish (a `completed` with a run still queued), it does not touch `finished_at`.
- `set_ingestion_status` and `set_ingestion_progress` do not touch the three keys. So the `progress_callback` step updates of a run keep its identity.

Both callers of `_set_running` hold `_status_lock`, and `_finish` updates under `_status_lock`. So each identity change happens in the same lock hold as the state change, and a reader never sees `running` with the previous run's `finished_at`.

A consequence: runs that queue behind a running run share its `run_id` and `started_at`, and the `completed` payload's span covers all of them. This is the run as a consumer sees it.

A `now` seam (a callable that returns an aware UTC `datetime`, default `datetime.now(timezone.utc)`) is an optional keyword-only argument of `build_ingestion_helpers`, so tests can fix the timestamps. The one production caller (`service_data_manager.py:46`) passes two positional arguments and is not changed.

### D2. Duration from timestamps, with a safe fallback

At the `completed` poll, the timestamps are **usable** only when all of these are true:

- `started_at` and `finished_at` of that payload parse with `datetime.fromisoformat` to aware datetimes;
- `finished_at >= started_at`;
- the wait saw no refresh or upload step (`_is_refresh_step(step)`) on any answered poll.

The third rule keeps the existing contract of `ingest_wall_seconds`: refresh and upload time is not ingest cost (`:1613-1622`, and the three refresh tests in `tests/unit/test_benchmark_ingest_wait.py`). Under D1, a refresh or upload in the same visible run is inside the timestamp span, so the timestamps then measure more than the corpus build. In that case the harness-observed span, which already excludes the refresh, is used.

A parse failure never raises (catch `TypeError` and `ValueError`): the wait must not fail a benchmark on a malformed optional field.

### D3. Missed-ingest detection

The wait records the `run_id` of the first answered poll and the harness start as a wall-clock UTC datetime. It gets the harness start from a new `wall_now` keyword seam, default `datetime.now(timezone.utc)`. The existing `clock` seam is monotonic and cannot compare with a timestamp. At the `completed` poll, when the ingest was never observed progressing (`ingest_started_at is None`), the ingest is "missed" if either:

- the `completed` payload's `run_id` is not `None` and differs from the first answered poll's `run_id`, or
- the first answered poll was itself `completed` and its `finished_at` parses to an aware time later than the harness start.

These are the two rules of the issue, and no more. A first poll that is `running` with the same `run_id` as the `completed` poll is not "missed": the wait cannot tell an unobserved ingest from a refresh in that case.

### D4. The source field and the value it pairs with

| Observed progressing | Missed (D3) | Timestamps usable (D2) | `ingest_wall_seconds` | `ingest_wall_source` |
|---|---|---|---|---|
| yes | — | yes | timestamp duration | `"status_timestamps"` |
| yes | — | no | observed span | `"harness_observed"` |
| no | yes | yes | timestamp duration | `"missed"` |
| no | yes | no | `None` | `"missed"` |
| no | no | — | `None` | `None` |

The issue asks that a missed ingest record the missed source, "not `null`". `"missed"` is kept even when the timestamps give a duration, because the reader must know that the harness did not watch this ingest. The duration is still exact in that case, so it is recorded.

The return type stays `Optional[float]`, so the existing tests and the `run()` call keep their contract. The method sets `self.ingest_wall_source = None` at its start and sets the final value before each return. `run()` reads it and passes `ingest_wall_source=` to `handle_results`, which writes it next to `ingest_wall_seconds`. Key absent in an artifact means the artifact predates the field.

## Risks / Trade-offs

- [Clock skew between the data-manager container and the benchmark host] → The duration uses two timestamps from the same container, so skew does not affect it. Skew affects only the D3 `finished_at > harness start` test; the `run_id` test does not depend on clocks.
- [A refresh or upload that the wait never sampled] → A refresh shorter than one poll interval (`BENCH_INGEST_POLL_INTERVAL`, default 5 s) can run inside the visible run between two polls. Then D2 uses the timestamps, and the duration includes that refresh. The error is at most about one poll interval for each unsampled refresh.
- [A refresh-only visible run before the first ingest] → If a scheduled refresh starts and finishes between two polls before the initial ingest, D3 sees a `run_id` change and records `"missed"`. In the benchmark flow the initial ingest is the first run the data manager starts, so this needs an unusual schedule. The reader sees `"missed"`, not a measured ingest.
- [An older data manager without the keys] → All new reads use `payload.get(...)`; the result is today's behavior with source `"harness_observed"` or `None`.
