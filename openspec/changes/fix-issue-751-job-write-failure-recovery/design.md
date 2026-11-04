## Context

`EvaluationJobManager` runs one job at a time. It keeps the job state in a JSON file
per job under `jobs_dir`, and an executor future per job in `self._futures`.
`_active()` (`jobs.py:90` on `origin/dev` `73104693`) reads the job files and returns
the first one whose status is `queued`, `running` or `cancel_requested`. `start()`,
`start_process()` and `continue_process()` call it under `self._lock` (an `RLock`,
`:43`) and raise `JobConflictError` when it returns a job.

A worker (`_execute` or `_execute_process`) writes each state change under the lock. If
one of those writes raises and the worker has no handler for it, the exception ends
the future, and the file keeps its last active status. No thread will write that record
again, so it blocks every later job until `_interrupt_stale_jobs()` runs at the next
service start.

`cancel()` (`:368`) writes `cancel_requested`, releases the lock, stops the process (or
waits for the future), and then, under the lock, calls `_finish_cancellation()`. That
function writes `completed_at`, calls `on_terminated` (the console writes
`canceled.json` there), and writes `canceled`. During this window the record must
block new jobs: `test_job_manager_terminates_running_evaluation_process`
(`test_jobs_history.py:470`) calls `start()` from `on_terminated` and expects
`JobConflictError` with `already cancel_requested`.

## Goals / Non-Goals

**Goals:**
- A record that no worker, no `cancel()` call and no tracked process owns does not
  block new jobs.
- Such a record moves to `failed` when a write succeeds, with the worker's error text
  when the worker raised.
- `cancel()` on such a record returns without raising and leaves it `canceled`.
- The `cancel_requested` window still blocks new jobs, also for such a record and also
  when two cancels of one job overlap.

**Non-Goals:**
- `get()` and `list()` do not repair records. A stuck record shows its stale status in
  the console until the next launch or cancel. These readers take no lock, and a write
  from a read path is a larger change.
- `start()` and `start_process()`: if `submit()` raises and the `failed` write after it
  raises too, the record has no future and keeps blocking. `submit()` raises only when
  the executor is shut down (`close()` or interpreter exit) or cannot start its single
  thread, and a second write failure must follow, so this is out of scope.
- `wait()` still raises a worker's exception. No code in `src/` calls it, and the new
  tests use it to wait for the worker to end.
- No new job status, and no change to `_interrupt_stale_jobs()`.

## Decisions

1. **Ownership rule, not "skip every done future".** An active record is ownerless
   when all three are true:
   - its future is in `self._futures` and is `done()`;
   - no `cancel()` call for the job is in progress (`self._cancel_calls[job_id] == 0`);
   - the job id is not in `self._processes`.

   The issue rules out "skip every record whose future is done", because the worker
   future ends before `_finish_cancellation()` runs. The second condition keeps that
   window blocking. The scratch run proved the point: a variant without the second
   condition fails `test_job_manager_terminates_running_evaluation_process` and two new
   tests. The rule does not depend on how the future ended. A worker that returns
   normally writes a terminal status first, unless it found the record
   `cancel_requested`. Such a record blocks while a `cancel()` call is in progress. If
   that call raised (for example, `on_terminated` failed), the record is ownerless, and
   the next `_active()` settles it. The same holds for a future that a cancel stopped
   before it started.
2. **Count the cancel calls for the whole call, in a wrapper.** `self._cancel_calls` is
   a `collections.Counter`. The public `cancel()` adds 1 under the lock, calls
   `_cancel()` (the old body, renamed), and subtracts 1 in a `finally` under the lock,
   deleting the key at 0. The old body keeps its lock structure byte for byte. Its
   checks, the `cancel_requested` write, the process and future lookups, and the
   before-start `future.cancel()` stay in one locked block, so no new unlocked gap
   exists. A count, not a set: if two `cancel()` calls for one job overlap (a double
   click), the first call to end does not release the record while the other is still
   in its window. Counting starts before the checks. A call that then fails a check
   (`LookupError`, `ValueError`, `JobConflictError`, or a raising `cancel_requested`
   write) only delays an ownerless settle until it returns, which is harmless.
3. **Keep the process guard.** If the manager still tracks a process for the job (for
   example, `process.wait()` raised in the worker), the record keeps blocking, and only
   `cancel()` can end it. This keeps the #435 rule that the manager never leaves a live
   worker process untracked. Every job-file write failure path removes the process from
   `_processes` before its failing write, so the guard never keeps a write-failure
   record blocking.
4. **Settle in `_active()`, best effort.** A new helper `_settle_ownerless(job)` returns
   `True` for an ownerless record. Before it returns, it writes the record as `failed`,
   with `completed_at` and `error` set. The error is `str(future.exception())` when the
   worker raised, else `"the job ended without a final status"` (the future returned
   normally, or a cancel stopped it before it started; `future.exception()` on a
   cancelled future raises `CancelledError`, so the code checks `future.cancelled()`
   first). If the write raises, the helper ignores the error and still returns `True`,
   and the next `_active()` call tries again. The record is not active either way, so a
   launch then writes its own `queued` record. If that write raises too, `start()`
   raises that error (HTTP 500 in the console), not a misleading 409. The helper returns
   `False` for a record whose `id` is not a string: `jobs_dir` is a host folder that a
   person can edit, and such an id must keep the old behavior (a conflict), not raise
   `TypeError` from a dict lookup.
5. **`failed`, not `interrupted`.** The issue's plan names `failed`. The docs table in
   `docs/docs/evaluation.md` defines `interrupted` as "the service restarted", which
   does not apply here.
6. **`future.exception(timeout=5)` instead of `future.result(timeout=5)`** in
   `_cancel()`. A hang still raises `TimeoutError`. A worker that raised has no process
   left to stop, so its error does not belong to the cancel call. The future cannot be
   cancelled at that point, because `future.cancel()` already returned `False`.
7. **Tests are staged, with totals.** Loop 1 ran each task's red and green stage on a
   scratch copy of `origin/dev` `73104693`, and six broken variants of the fix. Each
   variant fails a named test. The totals and the exact patches are in the `tasks.md`
   appendix. Apply them; do not rewrite them. Three independent reviewers checked an
   earlier version of this plan. They found a lock gap in a split `cancel()` (a second
   cancel could run `on_terminated` twice, and a worker that ended in the gap skipped
   the process-group kill), and four untested guards. This version removes the gap
   (decision 2) and adds a test for each guard.

## Risks / Trade-offs

- The full `tests/unit/evaluation/qa/test_jobs_history.py` takes about 10 s on an idle
  host. On a host with full swap and a load average above 100, four older tests that
  spawn real worker processes hit their 30 s wait and fail on `origin/dev` too. Run the
  gate on a quiet host. Do not change a timeout to make it pass.
- Two new tests use threads (`test_overlapping_cancels_...` and
  `test_a_failed_cancellation_before_start_...`). Every wait in them is bounded by
  `JOB_WAIT_TIMEOUT`, and none runs under the manager lock: `cancel()` calls
  `_terminate` without the lock, and the before-start test blocks the executor thread
  with its own task, not the lock.
- `_settle_ownerless()` writes inside `_active()`, which loops over a `glob`.
  `write_json` writes a temporary file whose name starts with a dot and then replaces
  the target, so the `[!.]*.json` pattern never matches the temporary file.
- A stuck record shows a stale status in the console until the next launch or cancel
  (Non-Goals). The docs paragraph says so.
