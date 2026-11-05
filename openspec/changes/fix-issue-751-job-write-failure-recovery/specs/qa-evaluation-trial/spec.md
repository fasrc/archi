## ADDED Requirements

### Requirement: A job record that nothing owns does not block new jobs
`EvaluationJobManager` SHALL treat a `queued`, `running` or `cancel_requested` job record as not active when its worker future is done, no `cancel()` call for that job is in progress, and the manager tracks no process for that job; before it skips such a record it SHALL try once to write it as `failed`, with the worker's error text when the worker raised, and a failure of that write SHALL NOT raise; a `cancel()` call SHALL keep its record active for the whole call, also when two calls for one job overlap, and SHALL NOT raise again the exception of a worker future that ended with one; a record whose `id` is not a string SHALL keep blocking as before.

#### Scenario: the next job starts once job-file writes recover
- **WHEN** every job-file write after the `queued` write raises, so that a process job's worker ends with an exception and its record stays `queued`, and writes then succeed again
- **THEN** `start()` does not raise `JobConflictError`, the new job completes, and the stuck record reads `failed` with the write error text and a `completed_at` value

#### Scenario: an atom-generation job whose running write failed
- **WHEN** the `running` write of a `generate_atoms` job raises and writes then succeed again
- **THEN** the next `start()` succeeds and the stuck record reads `failed` with the write error text

#### Scenario: the final write after the worker process exits fails
- **WHEN** the write after `process.wait()` raises, so the record stays `running`, and writes then succeed again
- **THEN** the next `start()` succeeds and the record reads `failed` with the write error text

#### Scenario: writes still fail
- **WHEN** a stuck record exists and every job-file write still raises
- **THEN** `start()` raises the write error, not `JobConflictError`, and the stuck record still reads `queued`

#### Scenario: cancel of a stuck job
- **WHEN** `cancel()` runs on a record whose worker future ended with an exception, and writes succeed
- **THEN** `cancel()` returns without raising and the record reads `canceled`

#### Scenario: the cancel window still blocks for a stuck job
- **WHEN** `on_terminated` calls `start()` during `cancel()` of a record whose worker future ended with an exception
- **THEN** `start()` raises `JobConflictError` with `already cancel_requested`

#### Scenario: a failed cancellation releases its job
- **WHEN** `on_terminated` raises during `cancel()` of a running process job, so the record stays `cancel_requested`, and the worker then ends
- **THEN** the next `start()` succeeds and the record reads `failed` with the error `the job ended without a final status`

#### Scenario: a failed cancellation before start releases its job
- **WHEN** `cancel()` stops a job's future before it started and `on_terminated` raises, so the record stays `cancel_requested`
- **THEN** the next `start()` succeeds, the record reads `failed` with the error `the job ended without a final status`, and no worker process was started

#### Scenario: overlapping cancels keep the job blocking
- **WHEN** two `cancel()` calls for one running job overlap and the second one raises from `on_terminated` while the first is still stopping the process
- **THEN** `start()` raises `JobConflictError` with `already cancel_requested`, and the first call then returns the record as `canceled`

#### Scenario: a tracked process keeps its job blocking
- **WHEN** a worker ends with an exception while the manager still tracks the job's process
- **THEN** `start()` raises `JobConflictError` and `cancel()` stops the process and leaves the record `canceled`

#### Scenario: a record with a non-string id
- **WHEN** a job file in `jobs_dir` reads `queued` and its `id` is not a string
- **THEN** `start()` raises `JobConflictError`, not `TypeError`
