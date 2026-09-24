## ADDED Requirements

### Requirement: A completed ingest run is recorded
The data manager SHALL write one ingest-run record when an ingest run reaches its completion point, carrying the run start time, the completion time, the resulting document and chunk counts, and a terminal status, so the corpus that serves queries can be traced to the run that built it.

#### Scenario: A finished run is recorded
- **WHEN** an ingest run completes its vectorstore update
- **THEN** one ingest-run row exists whose start and completion times bound the run and whose status marks it completed

#### Scenario: The newest completed run is identifiable
- **WHEN** several ingest runs have completed on a deployment
- **THEN** the most recently completed run is selectable without reading the others

### Requirement: The run record snapshots the ingest configuration
An ingest-run record SHALL carry a snapshot of the configuration that governed that run — HTML-to-Markdown enablement, categorization enablement, the chunking strategy, the embedding model and its dimensions, the chunk size and overlap, the distance metric, and the sitemap page floor — captured from the configuration in force during the run rather than read back later.

#### Scenario: The snapshot survives a later configuration change
- **WHEN** an ingest run completes, and the deployment is later redeployed with a different ingest configuration and no new ingest
- **THEN** the stored snapshot still reports the values that governed the completed run

#### Scenario: The snapshot covers the ingest-affecting flags
- **WHEN** an ingest-run record is written
- **THEN** its snapshot contains an entry for each ingest-affecting flag named in this requirement

### Requirement: Recording a run never breaks an ingest
A failure to write the ingest-run record SHALL be logged and SHALL NOT fail the ingest run or lose the ingested corpus, because the record is provenance and the corpus is the product.

#### Scenario: The record write fails
- **WHEN** the ingest-run write raises while the vectorstore update has already succeeded
- **THEN** the failure is logged and the ingest run still reports success
