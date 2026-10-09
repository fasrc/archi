## ADDED Requirements

### Requirement: The ingestion status carries a run identity and timestamps
The ingestion status payload SHALL carry `run_id`, `started_at`, and `finished_at` in addition to `state`, `step`, `error`, and `progress`, whose names and meanings SHALL NOT change; a run start SHALL set a new UUID4 `run_id`, set `started_at` to the current ISO-8601 UTC time, and clear `finished_at`, and a run end with `completed` or `error` SHALL set `finished_at` to the current ISO-8601 UTC time.

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
- **WHEN** two ingest runs occur one after the other
- **THEN** the second run's `run_id` differs from the first run's `run_id`
