## ADDED Requirements

### Requirement: QA runs record corpus provenance
A QA run that uses the vectorstore search tool SHALL record the corpus fingerprint at run start and when its attempts finish, plus the retrieval identity, in `manifest.json`, through the same fingerprint routine the golden-set harness uses; scoring SHALL copy those readings into `summary.provenance` and SHALL take no new reading; the retry path SHALL do the same.

#### Scenario: Run start
- **WHEN** the QA workflow starts a run whose agent spec includes `search_vectorstore_hybrid`
- **THEN** `manifest.json` contains `retrieval_identity` and `corpus_fingerprint_before`

#### Scenario: Attempts finish
- **WHEN** `run()` has executed its last attempt and before it marks the manifest `run_completed`
- **THEN** `manifest.json` contains `corpus_fingerprint` and `corpus_unchanged_at_endpoints`

#### Scenario: Scoring copies the readings
- **WHEN** the QA workflow writes `summary.json`
- **THEN** `summary.provenance` contains `corpus_fingerprint_before`, `corpus_fingerprint`, `corpus_unchanged_at_endpoints`, and `retrieval_identity` copied from the manifest, and no fingerprint query ran during scoring

#### Scenario: Retry with fresh attempts
- **WHEN** `retry()` executes new attempts against the vectorstore
- **THEN** the retry manifest contains the same start guard result, `corpus_fingerprint_before`, `corpus_fingerprint`, and `retrieval_identity`, and the retry summary copies them

#### Scenario: Run without the search tool
- **WHEN** the agent spec does not include `search_vectorstore_hybrid`
- **THEN** the run records no fingerprint, opens no vectorstore connection, and `retrieval_identity` is null
