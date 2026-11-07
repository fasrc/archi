## Context

`EvaluationJobManager.start_process` writes a `queued` job file and submits
`_execute_process` to a thread pool. `_execute_process` takes `self._lock`, checks for
`cancel_requested`, writes `running`, calls `subprocess.Popen`, and stores the process in
`self._processes`. All of that happens under the lock. `cancel()` takes the same lock, so
it cannot see `running` without a process. But `get()` reads the job file with no lock,
so a lock-free reader (the console, the test) can see `running` before the process
exists.

## Goals / Non-Goals

**Goals:** A reader that sees `running` from `get()` can always find the process in
`_processes`. A `Popen` failure records `failed` and leaves no `_processes` entry.

**Non-Goals:** The reaper behavior for a process that exits at once. A change to the
test's 30 s workflow or poll interval. A public `pid(job_id)` accessor (the test can keep
its read of `_processes`; add an accessor only if the test needs it).

## Decisions

1. **Move the write, do not add a status.** Write `running` and `started_at` after
   `self._processes[job_id] = process`, inside the same locked block. A new `starting`
   status was considered and rejected: every reader of `RUNNING` (`jobs.py:71`, `:98`,
   `:369`) and the console would need to learn it, for no user gain. `queued` already
   means "accepted, no process yet", and `cancel()` already handles `queued`.
2. **Keep the write under the lock.** The `cancel_requested` check, the `Popen`, the
   `_processes` store, and the `running` write stay in one critical section, so the
   ordering against `cancel()` does not change.
3. **Deterministic test through `write_json`.** Patch `jobs_module.write_json` with a
   wrapper that, each time it writes a job file whose status is `running`, records
   whether `manager._processes` already holds that job id. Patch
   `jobs_module.subprocess.Popen` with a stub that returns a fake process whose `wait()`
   blocks on a `threading.Event`. The test asserts that every recorded `running` write
   saw the entry. On today's code the first `running` write sees no entry, so the test is
   red with no timing dependence. Set the event and `close()` the manager at the end.
4. **Popen failure test.** Patch `Popen` to raise `OSError("boom")`. Wait for the job.
   Assert `status == "failed"`, `error` contains `boom`, `completed_at` is set, and the
   job id is not in `_processes`.

## Risks / Trade-offs

- `started_at` moves later by the fork/exec time (milliseconds). This is more accurate:
  it marks the time the process exists.
- If the fake process lacks a method that `_execute_process` or `close()` calls after
  `wait()`, the worker thread can raise. Read the code after `process.wait()` and give
  the fake process the attributes it uses (`pid`, `poll`, `wait`, `returncode`).
