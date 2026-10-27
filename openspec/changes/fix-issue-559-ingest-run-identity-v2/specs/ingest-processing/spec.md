## ADDED Requirements

### Requirement: The ingestion status carries a run identity and timestamps
The ingestion status payload SHALL carry `run_id`, `started_at`, and `finished_at` in addition to `state`, `step`, `error`, and `progress`, whose names and meanings SHALL NOT change; when the state becomes `running` from any other state, the status SHALL set a new UUID4 `run_id`, set `started_at` to the current ISO-8601 UTC time, and clear `finished_at` in the same lock hold as the state change, a publish of `running` while the state is already `running` SHALL keep all three keys, and a publish of `completed` or `error` SHALL set `finished_at` to the current ISO-8601 UTC time in the same lock hold.

#### Scenario: Keys exist before any run
- **WHEN** the status is read before any ingest run starts
- **THEN** `run_id`, `started_at`, and `finished_at` are present and `None`

#### Scenario: A run start sets the identity
- **WHEN** an ingest run starts
- **THEN** the status has a UUID4 `run_id`, an ISO-8601 UTC `started_at`, and `finished_at` is `None`

#### Scenario: A completed run sets the finish time
- **WHEN** an ingest run ends with `state` `completed`
- **THEN** `finished_at` is an ISO-8601 UTC time not earlier than `started_at`, and `run_id` is the value set at the start of the run

#### Scenario: A failed run sets the finish time
- **WHEN** an ingest run ends with `state` `error`
- **THEN** `finished_at` is set and `error` carries the failure message

#### Scenario: Step updates keep the identity
- **WHEN** the run reports a new `step` during the run
- **THEN** `run_id` and `started_at` do not change and `finished_at` stays `None`

#### Scenario: Two runs get different identities
- **WHEN** an ingest run publishes `completed` and a later run then starts
- **THEN** the second run's `run_id` differs from the first run's `run_id`

#### Scenario: A queued run shares the visible run
- **WHEN** a tracked run queues behind a run that is still `running` and gets the lock after that run finishes with `completed`
- **THEN** the queued run keeps the `run_id` and `started_at` of the visible run, and `finished_at` stays `None` until the last queued run publishes its terminal state

#### Scenario: A queued run after an error starts a new run
- **WHEN** a run publishes `error` while another run is queued, and the queued run then gets the lock
- **THEN** the queued run has a new `run_id` and a new `started_at`, and `finished_at` is `None` again
