## Context

`build_ingestion_helpers` (`src/utils/ingestion_status.py:16`) owns the status dict that `/api/ingestion/status` serves. Only `run_initial_ingestion_async` starts a run; `service_data_manager.py:202` runs it on a thread. The `progress_callback` it passes calls `set_ingestion_status("running", step=...)` many times during one run.

`Benchmarker.wait_for_ingestion_completion` (`src/bin/service_benchmark.py:2614`) polls the endpoint and returns the harness-observed span, or `None` when it never saw the ingest progress. `run()` (`:2348`) passes the value to `ResultHandler.handle_results` (`:502`), which writes `ingest_wall_seconds` into each arm record (`:638`).

## Goals / Non-Goals

**Goals:** an exact ingest duration when the data manager publishes timestamps; a distinct, recorded reading for "an ingest ran and the wait missed it"; additive payload and artifact changes only.

**Non-Goals:** the progress counter and stall budget (#558); background updates (#560); any change to the stall or ceiling budgets of the wait.

## Decisions

### D1. Run identity is set only at run start and run end

`run_initial_ingestion_async` sets `run_id = str(uuid.uuid4())`, `started_at = now`, and `finished_at = None` in the same locked update that sets `state = "running"`. It sets `finished_at` in the same locked update as `completed` or `error`. `set_ingestion_status` does not touch the three keys, so the `progress_callback` calls during a run keep the run identity. To make the end update atomic, add a private helper inside the closure that updates `state`, `step`, `error`, and `finished_at` under `_status_lock`.

A `now` seam (a callable that returns an aware UTC `datetime`, default `datetime.now(timezone.utc)`) is an optional keyword argument of `build_ingestion_helpers`, so tests can fix the timestamps. Timestamps are serialized with `isoformat()`.

### D2. Duration from timestamps, with a safe fallback

At the `completed` poll, parse `started_at` and `finished_at` of that payload with `datetime.fromisoformat`. Use the timestamps only when both parse to aware datetimes and `finished_at >= started_at`. Otherwise use the harness-observed span. A parse failure never raises: the wait must not fail a benchmark on a malformed optional field.

### D3. Missed-ingest detection

The wait records the `run_id` of the first answered poll and the harness start as a wall-clock UTC datetime (a new `wall_now` seam, default `datetime.now(timezone.utc)`; the existing `clock` seam is monotonic and cannot compare with a timestamp). At the `completed` poll, when the ingest was never observed progressing (`ingest_started_at is None`), the ingest is "missed" if either:

- the `completed` payload's `run_id` is not `None` and differs from the first answered poll's `run_id`, or
- the first answered poll was itself `completed` and its `finished_at` parses to a time later than the harness start.

### D4. The source field and the value it pairs with

| Observed progressing | Missed (D3) | Timestamps usable (D2) | `ingest_wall_seconds` | `ingest_wall_source` |
|---|---|---|---|---|
| yes | — | yes | timestamp duration | `"status_timestamps"` |
| yes | — | no | observed span | `"harness_observed"` |
| no | yes | yes | timestamp duration | `"missed"` |
| no | yes | no | `None` | `"missed"` |
| no | no | — | `None` | `None` |

The issue asks that a missed ingest record the missed source, "not `null`". `"missed"` is kept even when the timestamps give a duration, because the reader must know that the harness did not watch this ingest. The duration is still exact in that case, so it is recorded.

The return type stays `Optional[float]`, so the existing tests and the `run()` call keep their contract. The method sets `self.ingest_wall_source` before every return. `run()` reads it and passes `ingest_wall_source=` to `handle_results`, which writes it next to `ingest_wall_seconds`. Key absent in an artifact means the artifact predates the field.

## Risks / Trade-offs

- [Clock skew between the data-manager container and the benchmark host] → The duration uses two timestamps from the same container, so skew does not affect it. Skew affects only the D3 `finished_at > harness start` test; the `run_id` test does not depend on clocks.
- [Sibling #558 and #560 edit `ingestion_status.py`] → Additive keys only; whichever lands later rebases and keeps both.
- [An older data manager without the keys] → All new reads use `payload.get(...)`; the result is today's behavior with source `"harness_observed"` or `None`.
