## ADDED Requirements

### Requirement: A scheduled source refresh is reported as a running ingest
The data manager SHALL publish `state == "running"` with `step == "scheduled:<source>"` in the ingestion status payload while a scheduled refresh of `<source>` holds the ingestion lock, and SHALL publish `state == "completed"` when the refresh ends.

#### Scenario: Status during and after a git refresh
- **WHEN** `run_source_refresh("git", func, update_vectorstore, set_source_status)` runs and `func` reads the status
- **THEN** `func` sees `state == "running"`, `step == "scheduled:git"` and `progress` `null`
- **AND** after the call the status has `state == "completed"`

#### Scenario: The per-source status file is unchanged
- **WHEN** a scheduled refresh of `git` completes
- **THEN** `set_source_status` was called with `("git", state="running")` before `func`, and with `("git", state="idle", last_run=<UTC ISO-8601 string>)` after `update_vectorstore(force=True)`

### Requirement: An upload-triggered update is reported as a running ingest
The data manager SHALL publish `state == "running"` with `step == "upload"` while an upload-triggered `update_vectorstore(force=True)` holds the ingestion lock, and SHALL publish `state == "completed"` when it ends.

#### Scenario: Status during and after an upload update
- **WHEN** `run_tracked("upload", fn)` runs and `fn` reads the status
- **THEN** `fn` sees `state == "running"` and `step == "upload"`
- **AND** after the call the status has `state == "completed"`

### Requirement: A failed refresh or update is reported as an error
The data manager SHALL publish `state == "error"` with the exception message in `error` when a scheduled refresh or an upload-triggered update raises, and SHALL re-raise the exception to its caller.

#### Scenario: The update raises
- **WHEN** the function given to `run_tracked` raises `RuntimeError("boom")`
- **THEN** the status has `state == "error"` and `error == "boom"`
- **AND** `run_tracked` raises the same `RuntimeError`

### Requirement: The running status is published only after the ingestion lock is held
The data manager SHALL NOT change the ingestion status for a refresh or update that waits for the ingestion lock.

#### Scenario: A refresh queued behind a running ingest
- **WHEN** another thread holds the ingestion lock with `step == "embedding"` and a refresh calls `run_tracked`
- **THEN** the status keeps `step == "embedding"` until the other thread releases the lock

### Requirement: The initial ingest lifecycle is unchanged
The initial ingest SHALL keep its `pending` → `running` → `completed` or `error` lifecycle and its step values.

#### Scenario: Existing initial-ingest tests
- **WHEN** the existing tests in `tests/unit/test_ingestion_status_lock.py` run
- **THEN** they pass without changes to their assertions
