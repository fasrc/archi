## ADDED Requirements

### Requirement: The ingest configuration and drift come from the run that embedded the corpus
The Knowledge Base panel SHALL take the configuration it shows as "as used at ingest", and the configuration it compares for drift, from the newest completed ingest run with status `updated`, because an `up_to_date` run embeds nothing and its snapshot does not describe the corpus.

#### Scenario: An up_to_date run follows a config change
- **WHEN** the newest successful run has status `up_to_date` with snapshot Y, an older run has status `updated` with snapshot X, and the current configuration equals Y
- **THEN** the panel's configuration is X, and the drift list names every key whose value differs between Y and X, with Y's value as current and X's value at ingest

#### Scenario: The newest successful run is updated
- **WHEN** the newest successful run has status `updated`
- **THEN** the panel's configuration and drift come from that run's snapshot, and no further ingest-run query runs

#### Scenario: The run that embedded the corpus is older than the last ingest
- **WHEN** the panel's configuration comes from an `updated` run that is older than the newest successful run
- **THEN** the page shows that `updated` run's completion time under the configuration table, and a drift warning is titled "configuration changed after the corpus was built"

### Requirement: The last ingest stays the newest successful run
The Knowledge Base panel SHALL show the start time, finish time, duration, status and counts of the newest completed run with status `updated` or `up_to_date`, independent of which run supplies the configuration.

#### Scenario: Last ingest after an up_to_date run
- **WHEN** the newest successful run has status `up_to_date` and an older run has status `updated`
- **THEN** the panel's times, status and counts are those of the `up_to_date` run

### Requirement: No recorded embedding run is reported as unknown, not as no drift
When the newest successful run has status `up_to_date` and no completed `updated` run is recorded, the Knowledge Base panel SHALL show no configuration table and no drift, SHALL set `corpus_config_available` to `False`, and the page SHALL render text that says the configuration at ingest is unknown and drift was not evaluated.

#### Scenario: Only up_to_date runs are recorded
- **WHEN** the newest successful run has status `up_to_date` and the `updated`-run query returns no row
- **THEN** `config` is empty, `drift` is empty, `corpus_config_available` is `False`, and the page renders "Configuration at ingest unknown"

#### Scenario: The updated-run query raises
- **WHEN** the `updated`-run query raises
- **THEN** the failure is logged, the deployment panel and the last-ingest values still render, `corpus_config_available` is `False`, and the page text does not claim that no `updated` run is recorded
