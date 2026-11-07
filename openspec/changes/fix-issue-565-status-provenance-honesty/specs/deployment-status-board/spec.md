## ADDED Requirements

### Requirement: The corpus panel uses only a successful ingest run
The Knowledge Base panel SHALL take its counts and configuration only from the newest ingest run whose status is `updated` or `up_to_date`.

#### Scenario: A failed attempt is newer than the last successful run
- **WHEN** the newest completed `ingest_run` row has status `failed` and an older row has status `updated`
- **THEN** the panel shows the counts and configuration of the `updated` row

### Requirement: A newer failed ingest attempt stays visible
The Knowledge Base panel SHALL expose the time of the newest failed ingest attempt when that attempt is newer than the corpus run, or when no successful run exists, and the status page SHALL render it.

#### Scenario: Failed attempt after a successful run
- **WHEN** a `failed` row completed after the corpus run
- **THEN** the view model sets `last_attempt_failed_at` to that time and the page renders a failed-attempt warning

#### Scenario: Failed attempt older than the corpus run
- **WHEN** the newest `failed` row completed before the corpus run
- **THEN** `last_attempt_failed_at` is `None` and no failed-attempt warning renders

#### Scenario: Only failed attempts exist
- **WHEN** no successful run exists and a `failed` row exists
- **THEN** the panel is unavailable and the page renders the failed-attempt time

### Requirement: An unavailable current configuration is not reported as drift
When the current data-manager configuration cannot be read, the Knowledge Base panel SHALL report no drift and SHALL mark the current configuration as unavailable.

#### Scenario: The static_config row is absent
- **WHEN** the `static_config` query returns no row
- **THEN** `drift` is empty, `current_config_available` is `False`, and the page renders "drift not evaluated" text instead of a drift warning

#### Scenario: The static_config read raises
- **WHEN** the `static_config` query raises after the deployment and run rows were read
- **THEN** `drift` is empty and `current_config_available` is `False`

### Requirement: The pin-mismatch sentence renders only on a recorded mismatch
The Deployment panel SHALL say that the deployed config is not the pinned commit only when the recorded pin verdict is a mismatch.

#### Scenario: No pin verdict recorded
- **WHEN** `pin_matched` is `None` and dirty paths exist
- **THEN** `pin_mismatch` is `False` and the page does not render "The deployed config is not the pinned commit."

#### Scenario: Recorded mismatch
- **WHEN** `pin_matched` is `False`
- **THEN** `pin_mismatch` is `True` and the page renders the sentence

### Requirement: The provenance panels are documented
The services documentation SHALL describe the Deployment and Knowledge Base panels, both warnings, and their unknown states.

#### Scenario: An operator reads the status board docs
- **WHEN** an operator opens the "Service Status Board & Alert Banners" section of `docs/docs/services.md`
- **THEN** a subsection describes each panel row, the live-edited and drift warnings, the failed-attempt line, and the unavailable-configuration and no-verdict states
