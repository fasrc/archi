## ADDED Requirements

### Requirement: The ingestion status payload carries an embedding progress counter
The `/api/ingestion/status` payload SHALL carry a `progress` key that is `null` or an object `{"done": <int>, "total": <int or null>}`, and `state`, `step` and `error` SHALL keep their names and meanings.

#### Scenario: Progress is null before the embedding loop
- **WHEN** an ingest starts and has not yet reached the embedding database loop
- **THEN** the payload's `progress` is `null`

#### Scenario: Progress is reset at the start of each ingest
- **WHEN** a second ingest starts after an ingest that published a counter
- **THEN** the payload's `progress` is `null` before the second ingest publishes any counter

#### Scenario: The counter advances as batches commit
- **WHEN** the embedding loop commits batches of files during `update_vectorstore()`
- **THEN** the payload first shows `{"done": 0, "total": N}`, where N is the number of files queued for embedding
- **AND** after each commit `done` equals the number of files committed so far

#### Scenario: The existing keys are unchanged
- **WHEN** `set_ingestion_status(state, step=..., error=...)` is called
- **THEN** `state`, `step` and `error` are set exactly as before
- **AND** `progress` is not changed

### Requirement: A failing progress callback never aborts the ingest
The vectorstore manager SHALL log and ignore an exception raised by the embedding progress callback.

#### Scenario: The callback raises
- **WHEN** the embedding progress callback raises on every call
- **THEN** `_add_to_postgres` still commits every batch and returns normally

#### Scenario: No callback is given
- **WHEN** `update_vectorstore()` is called with no `embedding_progress` argument
- **THEN** it behaves exactly as before this change

### Requirement: The benchmark stall budget follows the progress counter when it is present
The benchmark ingest wait SHALL restart its stall budget on a `running` poll that carries a valid `progress.done` only when that value differs from the last accepted value, and SHALL keep the previous rule when the payload has no valid `progress.done`.

#### Scenario: A frozen counter trips the stall budget
- **WHEN** polls report `state=running`, a constant `step`, and a constant `progress.done` for longer than `BENCH_INGEST_WAIT_TIMEOUT`
- **THEN** the wait raises `TimeoutError` for a stall

#### Scenario: An advancing counter does not trip the stall budget
- **WHEN** polls report `state=running` and `progress.done` increases at intervals shorter than `BENCH_INGEST_WAIT_TIMEOUT`, for a total time longer than that budget
- **THEN** the wait does not raise and returns when `state=completed`

#### Scenario: A payload without progress behaves as before
- **WHEN** polls report `state=running` past `initializing` with no `progress` key, a `null` `progress`, or a malformed `progress`
- **THEN** every such poll restarts the stall budget, as before this change

#### Scenario: The queued and pending rules are unchanged
- **WHEN** polls report `state=pending`, or `state=running` with `step=initializing`, with any `progress` value
- **THEN** no such poll restarts the stall budget
