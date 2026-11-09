## ADDED Requirements

### Requirement: An upload is reported as a running ingest from the start of its collection
The data manager SHALL publish `state == "running"` with `step == "upload"` when a manual ingest through the uploader starts its collection, before any vectorstore update, SHALL NOT take the ingestion lock for the collection, SHALL NOT publish `completed` between the end of the collection and the end of its vectorstore update, and SHALL publish `completed` when the update ended and no other run is in flight.

#### Scenario: Status during the collection
- **WHEN** an upload is tracked with `track_upload()` and the status is read inside the block before any update
- **THEN** the status has `state == "running"`, `step == "upload"` and `progress` `null`
- **AND** after the block the status has `state == "completed"` and `step == "done"`

#### Scenario: No completed between the collection and the update
- **WHEN** the block runs `run_upload_update` after its collection and the status is read before and after that call
- **THEN** both reads have `state == "running"` and `step == "upload"`

#### Scenario: The collection does not wait for the ingestion lock
- **WHEN** a `run_tracked` refresh holds the ingestion lock with `step == "scheduled:git"` and an upload enters `track_upload()` on another thread
- **THEN** the block runs to its end while the lock is still held, the status inside it is `running` with `step == "scheduled:git"`, and the status stays `running` after the block until the refresh finishes with `completed`

#### Scenario: The run identity spans the collection and the update
- **WHEN** the block starts, runs `run_upload_update`, and ends
- **THEN** `run_id` and `started_at` are set at the block's start and unchanged after the update, `finished_at` is `null` until the block ends, and `finished_at` is set when the block ends

#### Scenario: The ingest ends while the upload still collects
- **WHEN** the initial ingestion published a step and an embedding counter, an upload entered `track_upload()` while it ran, and the ingestion ends before the block does
- **THEN** inside the block the status has `state == "running"`, `step == "upload"`, `progress` `null`, the ingestion's `run_id`, and `finished_at` `null`
- **AND** after the block the status has `state == "completed"` with `finished_at` set

#### Scenario: The collection ends while a refresh holds the lock
- **WHEN** a `run_tracked` refresh with `step == "scheduled:git"` starts inside the block and still runs when the block ends
- **THEN** the status keeps `step == "scheduled:git"` until the refresh finishes with `completed`

### Requirement: A failed upload never ends as completed
The data manager SHALL publish `state == "error"` with `step == "failed"` and the exception message when the tracked block raises, SHALL re-raise that exception, and SHALL keep an `error` published by the vectorstore update inside the block when the block ends normally.

#### Scenario: The collection raises
- **WHEN** the block raises `RuntimeError("crawl down")`
- **THEN** `track_upload()` re-raises it and the status has `state == "error"`, `step == "failed"` and `error == "crawl down"`

#### Scenario: The update inside the block fails
- **WHEN** `run_upload_update` inside the block raises because the sync returned `"failed"`, the caller swallows the exception, and the block ends normally
- **THEN** the status keeps `state == "error"` and `step == "failed"`, with an `error` that names `documents not added`

#### Scenario: The collection raises while the initial ingestion runs
- **WHEN** the block raises during the initial ingestion and the ingestion then publishes a step through its progress callback
- **THEN** the status after the raise has `state == "error"` with `finished_at` set, and the status after the step has `state == "running"`, `finished_at` `null`, and a `run_id` and `started_at` that differ from the failed run's

#### Scenario: An ingest that ends after a failed collection keeps the error
- **WHEN** one tracked collection raised during the initial ingestion, another is still inside its block, and the ingestion ends
- **THEN** the status keeps `state == "error"`, `step == "failed"` and the collection's message

### Requirement: Every manual-ingest handler runs inside the upload tracker
The uploader SHALL enter the data manager's upload tracker before the collection of `add_git_repo`, `add_jira_project`, `upload` and `upload_url` and before the deletes of `remove_git_repo`, SHALL leave it after `_finish_manual_ingest` returned, SHALL keep each handler's answers unchanged, and SHALL run the handlers without a tracker when none was given.

#### Scenario: Order of a successful manual ingest
- **WHEN** one of the five handlers runs with a recording tracker
- **THEN** the recorded order is `enter`, `collect`, `hook`, `exit`, and the answer is the handler's 200 body

#### Scenario: A failed collection leaves the tracker with the error
- **WHEN** the collector (for `remove_git_repo`, `delete_resource`) raises `RuntimeError("crawl down")`
- **THEN** the tracker sees the exception on exit, the update hook does not run, and the answer is the handler's own 500 (`ingest_failed`, `delete_failed` or `upload_failed`) with `detail == "crawl down"`

#### Scenario: A failed update leaves the tracker normally
- **WHEN** the update hook raises `RuntimeError("sync down")`
- **THEN** the recorded order is `enter`, `collect`, `hook`, `exit` and the answer is 500 `{"error": "update_failed", "detail": "sync down"}`

#### Scenario: No tracker
- **WHEN** the wrapper has no `track_upload` (the class default)
- **THEN** the recorded order is `collect`, `hook` and the answer is the handler's 200 body

#### Scenario: A not-found removal is not tracked
- **WHEN** `remove_git_repo` finds no document of the repository
- **THEN** the answer is 404 and the tracker was never entered

#### Scenario: The data manager hands its tracker to the uploader
- **WHEN** `service_data_manager.main()` builds the uploader
- **THEN** the uploader's `track_upload` is the data manager's, and a block of it publishes `running` / `upload` on `GET /api/ingestion/status` and `completed` after
