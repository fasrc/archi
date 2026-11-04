## Why

`EvaluationJobManager` (`src/evaluation/qa/jobs.py`) can block every new evaluation
console job until the service restarts (fasrc/archi#751). If a job-file write raises
inside a worker (for example, the disk is full), the exception ends the executor future
and the job file keeps its last active status. Nothing will ever finish that record:

- `_execute` writes `running` at `jobs.py:210`; a raise leaves the record `queued`.
- `_execute_process` writes `running` at `:260`. If that raises, the handler reaps the
  worker and writes `failed` at `:270`; if that write raises too, the record stays
  `queued`.
- `_execute_process` writes the final state at `:313`; a raise leaves the record
  `running`.

`_active()` (`:90`) returns the first job file whose status is `queued`, `running` or
`cancel_requested`, so `start()` and `start_process()` raise `JobConflictError` (HTTP
409 in the console) for every later launch. Only `_interrupt_stale_jobs()` (`:63`),
which runs when the service starts, clears such a record.

`cancel()` does not clear it either. It writes `cancel_requested`, finds no process, and
calls `future.result(timeout=5)` (`:399`). For a future that ended with an exception,
`result()` raises that exception again, so `cancel()` raises and the record stays
`cancel_requested`. The scratch run of the new tests confirmed this on `origin/dev`
`73104693` (`OSError: disk full` out of `cancel()`).

A `cancel()` that raises for another reason (for example, the console's `on_terminated`
callback cannot write `canceled.json`) leaves the record `cancel_requested` in the same
way, with no worker and no cancel left to finish it.

Origin: the Codex adversarial pass on PR #750 (fixes #435), deferred because the defect
is older than #750. Anchors verified on `origin/dev` `73104693` on 2026-11-04.

## What Changes

- **A record that nothing owns does not block.** `_active()` skips an active record when
  its worker future is done, no `cancel()` call for the job is in progress, and the
  manager tracks no process for it. Before it skips the record, it tries once to write
  it as `failed`, with the worker's error text when the worker raised. If that write
  raises too, the record is still skipped, and the next `_active()` call tries again.
- **`cancel()` owns its record for the whole call.** A new public `cancel()` adds 1 to
  an in-memory per-job counter, calls the old body (renamed `_cancel()`, otherwise
  unchanged), and subtracts 1 in a `finally`. While the count is above 0, the record
  blocks new jobs, also after the worker future is done. This keeps the window that
  `test_job_manager_terminates_running_evaluation_process` asserts, also when two
  cancels of one job overlap.
- **`cancel()` does not raise a dead worker's error.** `_cancel()` waits for the future
  with `future.exception(timeout=5)` instead of `future.result(timeout=5)`. A hang still
  raises `TimeoutError`. A worker that raised has nothing left to stop, so the cancel
  finishes and the record reads `canceled`.
- Eleven new unit tests in `tests/unit/evaluation/qa/test_jobs_history.py`. Eight fail
  on `origin/dev`. Three are guards that pass there and fail when a part of the fix is
  removed (the cancel count, the process rule, the id check).
- One paragraph in `docs/docs/evaluation.md` (troubleshooting, "The console says
  another job is active").

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `qa-evaluation-trial`: adds a requirement that a job record with no worker, no cancel
  in progress and no tracked process does not block new jobs, and that `cancel()` of
  such a record ends it.

## Impact

- `src/evaluation/qa/jobs.py`: `__init__` (one new `Counter`), `_active()`, a new
  `_settle_ownerless()` helper, and `cancel()` (a new wrapper around the old body,
  which becomes `_cancel()` with one changed line).
- `tests/unit/evaluation/qa/test_jobs_history.py`: three helpers and eleven tests.
- `docs/docs/evaluation.md`: one troubleshooting paragraph.
- No API change. No new job status. Under normal operation nothing changes: a worker
  that returns normally writes a terminal status (`completed`, `failed`,
  `attention_required`) before its future is done, unless it found the record
  `cancel_requested`. Such a record keeps blocking while a `cancel()` call is in
  progress, and only a cancel that raised leaves it for `_active()` to settle.
- The issue asks to keep the change inside `jobs.py` and its tests. The docs paragraph
  is the one exception: `AGENTS.md` requires a docs update for a change in user-facing
  behavior, and the console's "another job is active" troubleshooting text changes.
