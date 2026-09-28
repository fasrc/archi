## ADDED Requirements

### Requirement: QA runs record corpus provenance
A QA run that uses the vectorstore search tool SHALL record the corpus fingerprint at run start and at scoring end, plus the retrieval identity, in `manifest.json` and in `summary.provenance`, through the same fingerprint routine the golden-set harness uses.

#### Scenario: Run start
- **WHEN** the QA workflow starts a run whose agent spec includes `search_vectorstore_hybrid`
- **THEN** `manifest.json` contains `retrieval_identity` and `corpus_fingerprint_before`

#### Scenario: Scoring end
- **WHEN** the QA workflow writes `summary.json`
- **THEN** `summary.provenance` contains `corpus_fingerprint`, `corpus_unchanged_at_endpoints`, and `retrieval_identity`

#### Scenario: Run without the search tool
- **WHEN** the agent spec does not include `search_vectorstore_hybrid`
- **THEN** the run records no fingerprint, opens no vectorstore connection, and `retrieval_identity` is null
