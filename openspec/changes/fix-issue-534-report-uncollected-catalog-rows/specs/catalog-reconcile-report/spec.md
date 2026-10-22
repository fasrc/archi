## ADDED Requirements

### Requirement: A full ingestion pass SHALL report catalog rows that a successful scope did not collect

After `DataManager.run_ingestion` refreshes the catalog and before it resets or updates the vectorstore, the data manager MUST compute and log the catalog rows whose scope reported success in this pass but whose `resource_hash` this pass did not persist. A scope is the pair of the row's `source_type` and its scope key (git: repo name; web and sso: URL host; local_files: the whole source type; ticket: the ticket provider).

#### Scenario: Rows absent from a successful scope are candidates

- **WHEN** the git scope `User_Codes` persisted hashes `a` and `b` in this pass, recorded no failure, and the catalog holds rows `a`, `b`, `c`, and `d` for that scope
- **THEN** the candidates are exactly rows `c` and `d`

#### Scenario: Scope and collected sets agree

- **WHEN** a resource is persisted and a catalog row has the same metadata
- **THEN** both map to the same scope

### Requirement: A scope without a recorded success SHALL yield zero candidates

The pass MUST yield zero candidates for a scope when its collection recorded a failure, when it collected zero resources while the catalog holds rows for it, or when it belongs to a source type whose whole collection recorded a failure. For each such scope the pass MUST log one WARNING that names the scope and the reason. Indico and ELOG scopes MUST always be skipped this way, because their collectors expose no per-source success signal.

#### Scenario: A scope that failed

- **WHEN** a git clone failed for the repo `User_Codes` and the catalog holds rows for that repo
- **THEN** no row of that repo is a candidate
- **AND** one WARNING names the `git` scope `User_Codes` and the failure

#### Scenario: A scope that collected nothing

- **WHEN** the web host `docs.example.org` persisted zero resources while the web source ran, and the catalog holds 5 rows for that host
- **THEN** no row of that host is a candidate
- **AND** one WARNING names the host and says it collected 0 resources while the catalog holds 5 rows

#### Scenario: A failed SSO login

- **WHEN** the SSO login for seed host `h` returns no session and the crawl still yields the seed page unauthenticated
- **THEN** no `sso` row of host `h` is a candidate
- **AND** one WARNING names host `h`

#### Scenario: One ticket provider fails while the other succeeds

- **WHEN** the Jira client has no connection and Redmine persists tickets in this pass
- **THEN** no Jira row is a candidate
- **AND** one WARNING names the `ticket` scope `jira`

#### Scenario: A redirected crawl stops early

- **WHEN** a seed on host `h1` redirects to host `h2`, yields one `h2` page, and then stops at `max_pages` with `h2` pages still queued
- **THEN** no row of host `h2` is a candidate

#### Scenario: A failed child sitemap

- **WHEN** one child sitemap of a sitemap index fails to fetch and the other pages of its host are crawled
- **THEN** no row of that host is a candidate

#### Scenario: A swallowed per-page error

- **WHEN** a link crawl skipped one page on host `h` because of a fetch error
- **THEN** no row of host `h` is a candidate
- **AND** one WARNING names host `h`

### Requirement: Rows of a source type that did not run SHALL NOT be candidates

A source type that neither persisted a resource nor recorded a failure in this pass did not run. Its rows MUST NOT be candidates, and the pass MUST NOT log a WARNING for them.

#### Scenario: Tickets disabled

- **WHEN** ticket collection is disabled and the catalog holds `ticket` rows
- **THEN** no `ticket` row is a candidate and no WARNING names the `ticket` source type

### Requirement: The reconcile pass SHALL write nothing

The pass MUST NOT call any `delete_resource` method and MUST NOT run any SQL `UPDATE` or `DELETE` against `documents`. Its only database access is the read-only catalog query. An error inside the pass MUST be logged at WARNING and MUST NOT fail the ingestion.

#### Scenario: No write during the pass

- **WHEN** the pass runs with candidates present
- **THEN** no `delete_resource` call occurs and no `UPDATE` or `DELETE` statement reaches the catalog

#### Scenario: A defect in the report

- **WHEN** the catalog read raises during the pass
- **THEN** the pass logs a WARNING and `run_ingestion` continues to the vectorstore update

### Requirement: The reconcile pass SHALL log a summary, the candidate list, and skipped scopes

The pass MUST log one INFO line with the total candidate count, the counts by source type, and the counts by suffix. It MUST log each candidate (`resource_hash`, file path or URL, source type) at DEBUG. It MUST log each skipped scope at WARNING.

#### Scenario: Summary line

- **WHEN** the candidates are 4 `py`, 2 `sbatch`, and 2 `md` rows
- **THEN** one INFO line reports 8 candidates with those suffix counts

### Requirement: Only a full ingestion pass SHALL record collected resources

`PersistenceService.persist_resource` MUST record a persisted hash only while a collection pass is open. Only `DataManager.run_ingestion` opens a pass. Scheduled collections and chat-app uploads MUST NOT record anything.

#### Scenario: An aborted ingest discards its pass

- **WHEN** a collection step in `run_ingestion` raises
- **THEN** the exception still propagates
- **AND** no pass stays open, so a later scheduled collection records nothing

#### Scenario: Persist outside a pass

- **WHEN** `persist_resource` runs with no open pass
- **THEN** nothing is recorded and the call behaves as before
