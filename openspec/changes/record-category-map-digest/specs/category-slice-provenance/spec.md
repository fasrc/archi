## ADDED Requirements

### Requirement: One URL canonicalization rule for source matching and the category map
The system SHALL canonicalize a source URL with one shared rule — strip surrounding whitespace, then strip one trailing `/` from the URL path when the path is longer than `/`, keeping scheme, host, query and fragment unchanged — and the harness's reference-source matching and the category-map records SHALL both use it.

#### Scenario: Trailing slash on a KB article path
- **WHEN** the URL is `https://docs.rc.fas.harvard.edu/kb/running-jobs/`
- **THEN** the canonical form is `https://docs.rc.fas.harvard.edu/kb/running-jobs`

#### Scenario: Root path keeps its slash
- **WHEN** the URL is `https://docs.rc.fas.harvard.edu/`
- **THEN** the canonical form is `https://docs.rc.fas.harvard.edu/`

#### Scenario: Source matching is unchanged
- **WHEN** the harness matches a gold reference against retrieved metadata after the rule moves into `benchmark_provenance`
- **THEN** every case in `tests/unit/test_benchmark_source_url_match.py` gives the same result as before

### Requirement: Category-map digest is order-invariant and canonical
The system SHALL compute the category-map digest from one record per non-deleted document that has a URL, formatted as `<canonical_url>\t<category>` with an empty string for a missing category and with `%`, tab and newline percent-escaped, sorted, joined with `\n`, and hashed as `sha256:<hex>` of the UTF-8 bytes.

#### Scenario: Row order does not change the digest
- **WHEN** the same document rows are returned in two different orders
- **THEN** the two digests are equal

#### Scenario: A category change moves the digest
- **WHEN** one document's category changes and nothing else does
- **THEN** the digest changes

#### Scenario: Missing category and documents without a URL
- **WHEN** a document has a URL and no category, and another document has no URL
- **THEN** the first contributes `<canonical_url>\t` and the second contributes no record

#### Scenario: A separator inside a value cannot forge a record
- **WHEN** a category value contains a tab or a newline
- **THEN** the character is percent-escaped, so the record count equals the document count

### Requirement: Category-map readings at both arm endpoints, three-state
The system SHALL read the category map immediately before each arm's questions (beside the corpus-fingerprint reading in `Benchmarker.run()`) and again when the arm's results are handled (beside the end corpus-fingerprint reading in `handle_results`), SHALL record `category_map_sha256_start`, `category_map_sha256_end` and `category_map_unchanged_at_endpoints` in the arm entry, and SHALL set the last to `None` when either reading failed, else to the equality of the two digests.

#### Scenario: Unchanged map
- **WHEN** both readings succeed with equal digests
- **THEN** `category_map_unchanged_at_endpoints` is `true`

#### Scenario: Map edited during the arm
- **WHEN** both readings succeed with different digests
- **THEN** `category_map_unchanged_at_endpoints` is `false` and a warning names both digests

#### Scenario: A reading fails
- **WHEN** either reading raises (for example the Postgres factory is not initialized)
- **THEN** that digest is recorded as a string starting with `<unavailable:`, the run continues, and `category_map_unchanged_at_endpoints` is `null`

#### Scenario: Two failures never compare equal
- **WHEN** both readings fail with the same error text
- **THEN** `category_map_unchanged_at_endpoints` is `null`, not `true`

### Requirement: The end reading's records are persisted beside the artifact
The system SHALL write each arm's end-reading records, byte-for-byte the string that was hashed, to `<benchmark>-<timestamp>_category_map_<N>.tsv` in the artifact's output directory, where `<N>` is the arm's 1-based position in `benchmarking_results`, SHALL name that file in the arm entry as `category_map_file`, and SHALL NOT read the category map again when the artifact is written.

#### Scenario: File hash equals the end digest
- **WHEN** an arm's end reading succeeded and the artifact is written
- **THEN** `"sha256:" + sha256(file bytes)` equals that arm's `category_map_sha256_end`, and `category_map_file` is the file's basename

#### Scenario: Failed end reading writes no file
- **WHEN** an arm's end reading failed
- **THEN** no `.tsv` is written for that arm and `category_map_file` is `null`

#### Scenario: Multi-arm artifact
- **WHEN** one invocation runs three arms
- **THEN** three files `_category_map_1.tsv`, `_2.tsv`, `_3.tsv` share the artifact's stem, one per arm, each matching its own arm's end digest

### Requirement: Each arm records the digest of the prompt it ran
The system SHALL record in each arm entry `agent_md_sha256`, the sha256 of the agent prompt file named by that arm's `services.benchmarking.agent_md_file` as the harness read it for the arm, or `null` when the arm names no prompt file.

#### Scenario: Two arms, two prompts
- **WHEN** a sweep runs control and r0b
- **THEN** each arm entry carries the sha256 of its own prompt file, and the two values differ

### Requirement: The corpus fingerprint keeps its meaning
The system SHALL leave `CORPUS_STATE_QUERY`, `corpus_fingerprint`, `corpus_fingerprint_before`, `corpus_fingerprint` and `corpus_unchanged_at_endpoints` unchanged, so no archived artifact becomes incomparable with a new one.

#### Scenario: Fingerprint unaffected by a category edit
- **WHEN** only a document's category changes between two runs of an otherwise identical corpus
- **THEN** the two runs' `corpus_fingerprint` values are equal and their category-map digests differ
