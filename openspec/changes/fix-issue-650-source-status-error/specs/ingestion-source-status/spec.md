## ADDED Requirements

### Requirement: A failed scheduled refresh leaves its source status at error
The data manager SHALL write `state == "error"` to the per-source status of `<source>` when any step of a scheduled refresh of `<source>` raises, and SHALL re-raise the original exception.

#### Scenario: The collector raises
- **WHEN** `run_source_refresh("git", func, update_vectorstore, set_source_status)` runs and `func` raises `RuntimeError("collect down")`
- **THEN** the call raises `RuntimeError("collect down")`
- **AND** the last `set_source_status` call is `("git", state="error")`
- **AND** `update_vectorstore` was not called
- **AND** the global ingestion status has `state == "error"` and `error == "collect down"`

#### Scenario: The vectorstore sync returns failed
- **WHEN** `run_source_refresh("git", func, update_vectorstore, set_source_status)` runs and `update_vectorstore(force=True)` returns `"failed"`
- **THEN** the call raises `RuntimeError`
- **AND** the last `set_source_status` call is `("git", state="error")`
- **AND** no `set_source_status` call has `state == "idle"`

#### Scenario: A failed refresh does not advance last_run
- **WHEN** a scheduled refresh of `git` raises
- **THEN** no `set_source_status` call for `git` passes `last_run`

### Requirement: A failure to record the error status does not mask the refresh error
The data manager SHALL re-raise the original refresh exception when the write of `state == "error"` to the per-source status itself raises.

#### Scenario: The error write raises
- **WHEN** `func` raises `RuntimeError("collect down")` and `set_source_status` raises `OSError` for `state="error"`
- **THEN** `run_source_refresh` raises `RuntimeError("collect down")`, not `OSError`
