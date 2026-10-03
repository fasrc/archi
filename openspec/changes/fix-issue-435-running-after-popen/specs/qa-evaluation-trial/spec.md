## ADDED Requirements

### Requirement: An evaluation job reads as running only while its worker process exists
`EvaluationJobManager` SHALL write status `running` for a process-backed evaluation job only after `subprocess.Popen` returned and the process is stored in the manager's process table; if `Popen` raises, the job SHALL go to `failed` with the error and the process table SHALL hold no entry for that job.

#### Scenario: running is never visible before the process exists
- **WHEN** `_execute_process` writes a job file whose status is `running`
- **THEN** `manager._processes` already holds that job id

#### Scenario: the job stays queued during the fork/exec window
- **WHEN** `subprocess.Popen` is called for a job
- **THEN** the job file on disk still reads `queued`

#### Scenario: Popen failure
- **WHEN** `subprocess.Popen` raises `OSError` for a job
- **THEN** the job reads `failed`, its `error` holds the exception text, `completed_at` is set, and `manager._processes` has no entry for the job

#### Scenario: cancel of a running job finds the process
- **WHEN** `cancel()` runs on a job whose status is `running`
- **THEN** the manager finds the job's process in `_processes` and terminates it
