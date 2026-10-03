## Why

`test_job_manager_terminates_running_evaluation_process` is flaky (about 3 in 5 on a
loaded host) and blocks the commit gate (fasrc/archi#435). The flake exposes a real
ordering defect in `EvaluationJobManager._execute_process`
(`src/evaluation/qa/jobs.py:232`): it writes status `running` to the job file (`:238`)
**before** `subprocess.Popen` (`:242`) and before `self._processes[job_id] = process`
(`:258`). `get()` (`:394`) reads the job file without the lock, so a reader can see
`running` while no process exists. The test polls `get()` until it sees `running` and then
reads `manager._processes[job["id"]].pid` (`test_jobs_history.py:492`), which raises
`KeyError` inside the fork/exec window. Load widens the window.

The operator chose the production fix on 2026-09-26: `running` must mean "a live process
exists" for every reader. A test-only wait was rejected because it hides the defect.

Anchors verified on `origin/dev` `2a92d3c3` on 2026-10-03.

## What Changes

- In `_execute_process`, write `status = running` and `started_at` only after `Popen`
  succeeded and `self._processes[job_id]` holds the process. The write stays inside the
  same `with self._lock:` block, so `cancel()` (which takes the lock) still cannot
  interleave.
- If `Popen` raises, the job goes from `queued` to `failed` with the error, and no
  `_processes` entry exists (today it goes `running` → `failed`).
- No new status value. The job stays `queued` during the fork/exec window.
- Add a deterministic unit test for the ordering, and a unit test for the `Popen`
  failure path.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `qa-evaluation-trial`: adds a requirement on the evaluation job status lifecycle.

## Impact

- `src/evaluation/qa/jobs.py` (`_execute_process` only).
- `tests/unit/evaluation/qa/test_jobs_history.py` (two new tests).
- The console shows `queued` for a few more milliseconds before `running`. No API
  change. `cancel()` already accepts `queued`.
