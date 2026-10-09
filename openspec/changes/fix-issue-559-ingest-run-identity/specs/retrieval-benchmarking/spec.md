## ADDED Requirements

### Requirement: The benchmark records the ingest duration and its source
The benchmark SHALL record `ingest_wall_source` next to `ingest_wall_seconds` in each arm record, with the value `"status_timestamps"`, `"harness_observed"`, `"missed"`, or `null`; it SHALL compute the duration from the `completed` payload's `started_at` and `finished_at` when both parse and `finished_at` is not earlier than `started_at`, and SHALL otherwise use the harness-observed span.

#### Scenario: Timestamps give the duration
- **WHEN** the wait observes the ingest progress and the `completed` payload has `started_at` and `finished_at` 300 seconds apart
- **THEN** `ingest_wall_seconds` is 300 and `ingest_wall_source` is `"status_timestamps"`

#### Scenario: No timestamps falls back to the observed span
- **WHEN** the wait observes the ingest progress and the `completed` payload has no `started_at` or `finished_at`
- **THEN** `ingest_wall_seconds` is the harness-observed span and `ingest_wall_source` is `"harness_observed"`

#### Scenario: Malformed timestamps fall back to the observed span
- **WHEN** a timestamp does not parse, or `finished_at` is earlier than `started_at`
- **THEN** the wait does not raise, and `ingest_wall_source` is `"harness_observed"`

#### Scenario: A run_id change without progress is a missed ingest
- **WHEN** the wait never sees the ingest progress and the `completed` payload's `run_id` differs from the `run_id` of the first answered poll
- **THEN** `ingest_wall_source` is `"missed"`, not `null`

#### Scenario: A fresh finish time on the first poll is a missed ingest
- **WHEN** the first answered poll is `completed` with a `finished_at` later than the harness start
- **THEN** `ingest_wall_source` is `"missed"`, and `ingest_wall_seconds` is the timestamp duration when both timestamps parse

#### Scenario: An already-built corpus records no ingest
- **WHEN** the first answered poll is `completed` with a `finished_at` earlier than the harness start, or with no timestamps and no `run_id` change
- **THEN** `ingest_wall_seconds` is `null` and `ingest_wall_source` is `null`

#### Scenario: The arm record carries the source
- **WHEN** the benchmark writes an arm record
- **THEN** the record contains `ingest_wall_source` with the value the wait recorded
