## ADDED Requirements

### Requirement: The status board shows the deployed config pin
The service status board SHALL display the config pin the running deployment was provisioned with — `CONFIG_REF`, `CONFIG_SHA`, the config `HEAD` that actually deployed, and the deploy timestamp — so an operator can confirm from the application which config is live, without reading deploy output on the host.

#### Scenario: A clean on-pin deploy shows the pin
- **WHEN** an operator opens `/ssb/status` after a deploy whose config `HEAD` equalled `CONFIG_SHA` with no dirty paths
- **THEN** the page shows the pin ref, the short commit, the deploy timestamp, and a state indicating the deployed config matched the pin

#### Scenario: The application version is shown beside the pin
- **WHEN** the deployment record carries a non-empty `app_version`
- **THEN** the page shows that value in the Deployment panel

### Requirement: A live-edited config is flagged, never presented as the pin
The status board SHALL show a live-edited warning whenever the deployed config `HEAD` did not match `CONFIG_SHA`, or matched it while carrying tracked edits, and the warning SHALL be visually distinct from the matched state, because a deploy is permitted in both cases and the pin constant alone would misrepresent what is running.

#### Scenario: Tracked edits on the pin raise the warning
- **WHEN** the deployment record reports `pin_matched` true and a non-empty `dirty_paths`
- **THEN** the Deployment panel shows the live-edited warning and the count of dirty paths, instead of presenting the pin as the deployed config

#### Scenario: An off-pin deploy raises the warning
- **WHEN** the deployment record reports `pin_matched` false
- **THEN** the Deployment panel shows the live-edited warning and the actual deployed `HEAD`

### Requirement: The status board shows the knowledge-base ingest state
The service status board SHALL display the last completed ingest window, the document counts by ingestion status, and the chunk count, so an operator can tell when the corpus was last built and whether any documents failed.

#### Scenario: Counts come from the document catalogue
- **WHEN** an operator opens `/ssb/status` on a deployment with an ingested corpus
- **THEN** the Knowledge base panel shows the ingest start and finish times, the run duration, and the number of documents that are embedded, failed and pending

### Requirement: Ingest configuration is shown as it was at ingest
The status board SHALL render the ingest configuration from the snapshot recorded by the ingest run that produced the corpus, and SHALL NOT present current configuration as though it described that corpus, because configuration can change after an ingest without the corpus being rebuilt.

#### Scenario: The snapshot is the source of the flag values
- **WHEN** the last completed ingest run carries a configuration snapshot
- **THEN** the flag values shown in the Knowledge base panel are the snapshot's values

### Requirement: Configuration drift after an ingest is flagged per flag
The status board SHALL compare the current ingest configuration against the last completed run's snapshot and, when they differ, SHALL show a drift warning that names each differing flag with both its current value and its value at ingest.

#### Scenario: A flag flipped after the last ingest
- **WHEN** the current ingest configuration disables a flag that was enabled in the last run's snapshot, and no ingest has completed since
- **THEN** the Knowledge base panel shows the drift warning and lists that flag with its value now and its value at ingest

#### Scenario: No drift when configuration is unchanged
- **WHEN** the current ingest configuration equals the last run's snapshot
- **THEN** no drift warning is shown

### Requirement: A missing record degrades the panel without failing the page
The status board SHALL render, and SHALL continue to show the alert sections, when a deployment record or an ingest run record is absent or unreadable, showing the affected panel as unavailable rather than raising an error, because an existing deployment carries no record until its next deploy or ingest.

#### Scenario: A deployment that predates the record
- **WHEN** no deployment-record row exists
- **THEN** the page renders, the Deployment panel reports that provenance is unavailable, and the Active Alerts section is unaffected

#### Scenario: A database failure while loading provenance
- **WHEN** the provenance query raises
- **THEN** the failure is logged, the page still renders, and the alert sections still display
