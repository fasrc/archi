## ADDED Requirements

### Requirement: Every evaluation run SHALL record whether its embedding tags changed between the start guard and the end
When a run that searches the collection finishes its questions, it SHALL read the tag state again (`embedding_model_tags` and `untagged_chunk_count`, through the same query as the start guard) and SHALL record it as `embedding_tags_end`.
It SHALL record `embedding_tags_unchanged_at_endpoints` as `false` when the end tag list holds a tag other than the start record's `embedding_model`, or the end `untagged_chunk_count` is greater than the start record's; as `null` when the end reading is unavailable or the start record lacks those fields; and as `true` otherwise. The tag SHALL NOT enter the corpus fingerprint digest. One shared helper computes the state and the comparison for the golden-set harness and the QA workflow.

#### Scenario: Golden-set arm whose tags did not change
- **WHEN** a golden-set arm with `embedding_model` `m1` and 0 untagged chunks at the start ends with the tag list `["m1"]` and 0 untagged chunks
- **THEN** the arm's record has `embedding_tags_end == {"embedding_model_tags": ["m1"], "untagged_chunk_count": 0}` and `embedding_tags_unchanged_at_endpoints == true`

#### Scenario: Golden-set arm re-embedded under another model during the arm
- **WHEN** a golden-set arm with `embedding_model` `m1` ends with the tag list `["m1", "m2"]`, and both corpus fingerprints are equal
- **THEN** the arm records `corpus_unchanged_at_endpoints == true` and `embedding_tags_unchanged_at_endpoints == false`, and the harness logs a warning

#### Scenario: Untagged count rose during the arm
- **WHEN** an arm starts with 0 untagged chunks and ends with 5 untagged chunks
- **THEN** the arm records `embedding_tags_unchanged_at_endpoints == false`

#### Scenario: Same-model ingestion into a legacy collection
- **WHEN** an arm with `embedding_model` `m1` starts on a collection whose chunks are all untagged, and ingestion adds chunks tagged `m1` during the arm without raising the untagged count
- **THEN** the arm records `embedding_tags_unchanged_at_endpoints == true`

#### Scenario: End reading fails
- **WHEN** the end tag reading raises
- **THEN** the arm keeps its scores, `embedding_tags_end` is an `<unavailable: …>` marker, and `embedding_tags_unchanged_at_endpoints` is `null`

#### Scenario: QA run re-embedded while it answered
- **WHEN** a QA run whose agent uses the search tool starts with `embedding_model` `m1` and its end reading returns the tag list `["m2"]`
- **THEN** `manifest.json` and `summary.provenance` record `embedding_tags_unchanged_at_endpoints == false`

#### Scenario: QA run without the search tool
- **WHEN** a QA run's agent spec does not use the search tool
- **THEN** `embedding_tags_end` and `embedding_tags_unchanged_at_endpoints` are both `null` and no tag reading is made

#### Scenario: QA run answered before this change is scored after it
- **WHEN** a QA manifest that has no `embedding_tags_unchanged_at_endpoints` key is scored, or retried without fresh attempts
- **THEN** neither `summary.provenance` nor the retry manifest gains that key

### Requirement: Comparison, archive, and report tooling SHALL refuse or flag a run whose embedding tag state changed
A run that records `embedding_tags_unchanged_at_endpoints` with a value other than `true` SHALL be refused by `compare_runs.py` as an arm, as a noise replicate, and as a `--qa-run`, with no flag admitting it; SHALL be refused by `archive_run.sh` in single-arm mode (exit 2) and in `--sweep` mode; SHALL make the harness withhold leaderboard ranks and warn; and SHALL be flagged in the per-arm HTML and Markdown reports.
A run that does not record the key at all predates it and SHALL be treated as before.

#### Scenario: compare_runs arm with changed tags
- **WHEN** an arm records `embedding_tags_unchanged_at_endpoints: false` and both arms otherwise pass every gate
- **THEN** `compare_runs.py` exits with the gate exit code, the reason names that arm and the tag change, and `--corpus-differs-by-design` does not admit it

#### Scenario: compare_runs noise replicate with changed tags
- **WHEN** a `--noise-runs` replicate records `embedding_tags_unchanged_at_endpoints: false`
- **THEN** `compare_runs.py` exits with the gate exit code before the noise floor is computed

#### Scenario: compare_runs legacy arm
- **WHEN** no arm records `embedding_tags_unchanged_at_endpoints`
- **THEN** the comparison runs as it did before this change

#### Scenario: compare_runs QA run with changed tags
- **WHEN** a `--qa-run` summary records equal corpus readings and `embedding_tags_unchanged_at_endpoints: false`
- **THEN** the QA run is refused with a reason that says the embedding model tags changed while it answered

#### Scenario: archive_run refuses changed tags
- **WHEN** `archive_run.sh` reads an artifact with equal usable fingerprints, `corpus_unchanged_at_endpoints: true`, and `embedding_tags_unchanged_at_endpoints: false`
- **THEN** it prints a `REFUSED:` line that names the tag change, exits 2, and writes no ledger row and no pin

#### Scenario: archive_run sweep mode refuses changed tags
- **WHEN** `archive_run.sh --sweep` archives an artifact one of whose arms records `embedding_tags_unchanged_at_endpoints: false`
- **THEN** the archive fails with a message that names that arm and the tag change, and no pin is written

#### Scenario: Harness leaderboard withholds ranks
- **WHEN** one arm of a sweep records `embedding_tags_unchanged_at_endpoints: false`
- **THEN** `arms_incomparability_reason` returns a reason that names the tag change, and the leaderboard lists a warning for that variant

#### Scenario: Per-arm report flags the change
- **WHEN** the per-arm report is generated for an arm that records `embedding_tags_unchanged_at_endpoints: false`
- **THEN** the HTML and the Markdown reports each show an alert that the embedding model tags changed during the run
