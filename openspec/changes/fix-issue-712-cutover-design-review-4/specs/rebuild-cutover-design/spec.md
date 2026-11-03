## ADDED Requirements

### Requirement: The cutover design gives each scheduled retry a process that runs it
The rebuild cutover design (`docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md`) SHALL state that a run of the recovery job in the process that holds the lifecycle connection does the next step of the stored state on that connection when no step of that lifecycle is in progress, so that a cutover retry after 3 lock timeouts and a rollback retry after a lock timeout always have a process that runs them.

Sections 4.5, 4.6, 4.10, 5 and 6 of the design state the same rule. No section says that the recovery job returns at once because its process has a lifecycle connection.

#### Scenario: The cutover times out 3 times in the owner process
- **WHEN** a reader follows sections 4.5 and 4.10 for a build that stays `validating` after 3 cutover lock timeouts
- **THEN** the design says that the recovery job of the process that holds the locks does the cutover again on its lifecycle connection, and opens no second connection

#### Scenario: A step of the lifecycle is in progress
- **WHEN** the recovery job runs in the owner process while the build, a cutover, the probe, or a rollback runs
- **THEN** the design says that the job returns, and that the running step continues the lifecycle

#### Scenario: The old rule is gone
- **WHEN** `grep -n "Returns at once if this process already has a lifecycle connection"` runs on the design
- **THEN** it prints nothing

### Requirement: The cutover design makes the final sync a durable step
The rebuild cutover design SHALL state that the transaction that moves a build to an end state also sets `index_build.phase = 'sync_pending'`, that the code clears the phase after `update_vectorstore()` returns, and that the recovery job runs the final sync for a row in that phase.

The design also states that a `sync_pending` phase does not defer an incremental write, because the final sync is itself an incremental write.

#### Scenario: The final sync raises in the same process
- **WHEN** a reader follows section 4.10 for a final sync that raises after the end state commits
- **THEN** the design says that the row keeps `phase = 'sync_pending'` and that the next run of the recovery job runs the sync again and clears the phase

#### Scenario: An upload arrives while a row is sync_pending
- **WHEN** a reader follows section 4.3 step 1 for an incremental write while a row has `phase = 'sync_pending'`
- **THEN** the design says that the write is not deferred

#### Scenario: The phase is in the schema, the table, and the tests
- **WHEN** `grep -n "sync_pending"` runs on the design
- **THEN** it prints the `phase` column comment of section 4.2, a row of the section 4.10 table, and a section 6 test line

### Requirement: The cutover design skips the shrink check only for a removed or changed source
The rebuild cutover design SHALL record one hash for each source input in the build identity (`source_entries`), and SHALL skip the chunk-ratio check of section 4.4 only when an entry of the live row is absent from the build or when the live value is `unknown`.

A build that keeps every entry of the live set, also with new entries, keeps the chunk-ratio check. The design has no single `source_digest` field.

#### Scenario: A redeploy adds a source while an existing source returns no documents
- **WHEN** a reader follows section 4.4 for a build whose identity has every live entry plus one new entry, and whose chunk count is below the ratio
- **THEN** the design says that the ratio check applies and that the gate rejects the build

#### Scenario: A redeploy removes a source
- **WHEN** a reader follows section 4.4 for a build whose identity lacks one entry of the live row
- **THEN** the design says that the ratio check is skipped and that the other checks still apply

#### Scenario: The single digest is gone
- **WHEN** `grep -c "source_digest"` runs on the design
- **THEN** it prints `0`

### Requirement: The cutover design records the embedder options that change vectors
The rebuild cutover design SHALL record in the build identity an `embedding_options` field that holds the allowlisted constructor kwargs that change the stored vectors, starting with `encode_kwargs.normalize_embeddings`, and SHALL use that field in change detection (4.7), in the `validating` check (4.3 step 2a), and in the reader (4.8).

The allowlist names no credential. The baseline row records `embedding_options` as `unknown`.

#### Scenario: The normalization option changes
- **WHEN** a reader follows section 4.7 for a redeploy that changes only `encode_kwargs.normalize_embeddings`, with `reset_collection: false`
- **THEN** the design says that a cutover build starts

#### Scenario: Chat reads the live set during that build
- **WHEN** a reader follows section 4.8 for a query while that build runs
- **THEN** the design says that chat builds the embedder of the live row with the `embedding_options` of that row, and takes credentials from the current class-map entry

#### Scenario: The rule and its test are in the design
- **WHEN** `grep -n "normalize_embeddings\|encode_kwargs"` runs on the design
- **THEN** it prints the section 4.2 identity rule and a section 6 test line
