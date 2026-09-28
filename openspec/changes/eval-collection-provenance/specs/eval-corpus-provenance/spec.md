## ADDED Requirements

### Requirement: Every evaluation run records its retrieval identity
Every evaluation run SHALL record the collection tag it searched, the embedding class name, and the embedding model identifier, derived from the run's configuration by one shared helper that uses the same collection formula as `VectorstoreConnector`.

#### Scenario: Golden-set arm records the identity
- **WHEN** the golden-set harness finishes an arm
- **THEN** the arm's record contains `retrieval_identity` with `collection`, `embedding_name`, `embedding_model`, `chunk_count`, `usable_chunk_count`, `untagged_chunk_count`, and `embedding_model_source`

#### Scenario: QA run records the identity
- **WHEN** the QA workflow starts a run whose agent spec uses the vectorstore search tool
- **THEN** `manifest.json` contains `retrieval_identity` with the same fields

#### Scenario: Sweep ledger row carries the identity
- **WHEN** `archive_run.sh`, `qa_arm.sh`, or `sweep_tools.py` appends a ledger row for an arm
- **THEN** the row contains `collection` and `embedding_model` copied from the arm's artifact or run manifest

#### Scenario: Embedding model comes from the class-specific kwarg
- **WHEN** the config's `embedding_name` is `HuggingFaceEmbeddings` with `kwargs.model_name` set, or `OpenAIEmbeddings` with `kwargs.model` set
- **THEN** `embedding_model` equals that kwarg value, and when neither kwarg exists it equals the class name

### Requirement: The corpus fingerprint covers the searched collection and only rows retrieval can reach
The corpus fingerprint SHALL hash only chunks that the searched collection's retrieval filter admits, only parent nodes that such a chunk references, and only live documents that own such a chunk; it SHALL encode every row value as a JSON array of fields; it SHALL include each chunk's `collection` tag and null-vector flag, and the citation fields (URL, display name, source type, title, filename) from the document row for a linked chunk and from the row's own metadata for a documentless chunk and for a parent node; it SHALL NOT include `size_bytes`, the `embedding_model` tag, or any other `extra_json` field.

#### Scenario: Ingest into another collection leaves the digest unchanged
- **WHEN** chunks are added under a different `collection` tag
- **THEN** the fingerprint of the searched collection is unchanged

#### Scenario: A collection move changes the digest
- **WHEN** one chunk's `collection` tag changes and nothing else changes
- **THEN** the fingerprint changes

#### Scenario: Orphan parent nodes do not affect the digest
- **WHEN** parent nodes that no chunk references are added or deleted
- **THEN** the fingerprint is unchanged

#### Scenario: A chunk with no document link is covered
- **WHEN** the text of a chunk with `document_id IS NULL` changes
- **THEN** the fingerprint changes

#### Scenario: Retrieval-visible document fields are covered
- **WHEN** a document's `url`, `display_name`, `source_type`, or `extra_json->>'title'` changes and nothing else changes
- **THEN** the fingerprint changes

#### Scenario: Re-embed with another model is not a corpus change
- **WHEN** identical chunk text is re-embedded and every chunk's `embedding_model` tag changes from A to B
- **THEN** the fingerprint is unchanged and `retrieval_identity.embedding_model` differs between the two runs

#### Scenario: Document with a NULL url is covered
- **WHEN** a document has `url IS NULL` and its title changes
- **THEN** the fingerprint changes

#### Scenario: Field boundaries are unambiguous
- **WHEN** one document has `display_name = "a|b"`, `source_type = "c"` and another state has `display_name = "a"`, `source_type = "b|c"`, with every other field equal
- **THEN** the two fingerprints differ

#### Scenario: Documentless chunk citation metadata is covered
- **WHEN** the `url` in the metadata of a chunk with `document_id IS NULL` changes
- **THEN** the fingerprint changes

#### Scenario: Parent node metadata is covered
- **WHEN** the `title` in a referenced parent node's metadata changes
- **THEN** the fingerprint changes

#### Scenario: Size-only change is not a corpus change
- **WHEN** a document's `size_bytes` changes and no chunk, parent, or citation field changes
- **THEN** the fingerprint is unchanged

#### Scenario: Category-only change is not a corpus change
- **WHEN** only `extra_json->>'category'` changes on a document
- **THEN** the fingerprint is unchanged

#### Scenario: A vector set to NULL is covered
- **WHEN** one in-scope chunk's `embedding` becomes `NULL` and nothing else changes
- **THEN** the fingerprint changes

#### Scenario: Digest carries a version prefix
- **WHEN** the fingerprint is computed
- **THEN** the value starts with `sha256/v2:`

### Requirement: The category-map digest covers the searched collection only
The URL-to-category map that a run records SHALL cover only documents that own at least one chunk the searched collection's retrieval filter admits.

#### Scenario: Relabel in another collection
- **WHEN** a document with chunks only in another collection changes its `category`
- **THEN** the run's category-map digest is unchanged

#### Scenario: Relabel in scope
- **WHEN** a document with an in-scope chunk changes its `category`
- **THEN** the run's category-map digest changes

### Requirement: One routine computes the fingerprint for every consumer
The golden-set harness, the QA workflow, the feature-matrix sweep, and the category census SHALL compute the corpus fingerprint and the category map through functions in `src/utils/benchmark_provenance.py`: a row layer that runs on the caller's cursor and a convenience layer that takes the configuration as a required argument; no consumer SHALL read the query text out of another module's source.

#### Scenario: Sweep and harness agree by construction
- **WHEN** the feature-matrix pin check and the harness compute the fingerprint of one unchanged stack
- **THEN** the two digests are equal

#### Scenario: Census reads one snapshot
- **WHEN** the category census reads the category map and the corpus state
- **THEN** both queries run on one `REPEATABLE READ` cursor, and the census keeps its `(url, category)` tuples

#### Scenario: Container snippet installs the factory
- **WHEN** the feature-matrix snippet runs inside the stack's data-manager container
- **THEN** it installs the Postgres factory it builds before it reads the config, and the digest equals the harness's digest for the same stack

#### Scenario: Stack image predates the routine
- **WHEN** the feature-matrix snippet cannot import the routine inside the stack container
- **THEN** it exits with a message that names the missing routine and tells the operator to rebuild the stack from the campaign SHA

### Requirement: A run refuses to start against an empty or mismatched collection
An evaluation run SHALL stop before its first question when the searched collection contains zero chunks, or zero chunks with a non-null vector, or when any chunk in it carries an `embedding_model` tag that differs from the run's embedding model, and it SHALL warn but continue when the chunks carry no tag.

#### Scenario: Zero chunks
- **WHEN** the searched collection has zero chunks
- **THEN** the run raises before any question is sent, and the message names the collection and the embedding name

#### Scenario: Rows exist but no row has a vector
- **WHEN** the searched collection has chunks and every chunk's `embedding` is `NULL`
- **THEN** the run raises before any question is sent, and the message gives both `chunk_count` and `usable_chunk_count`

#### Scenario: Chunk model differs from query model
- **WHEN** a chunk in the collection carries `embedding_model = A` and the run's `embedding_model` is `B`
- **THEN** the run raises before any question is sent, and the message names both values

#### Scenario: Legacy chunks without a tag
- **WHEN** every chunk in the collection has no `embedding_model` tag
- **THEN** the run continues, logs a warning, and records `embedding_model_source = "config (chunks untagged)"`

#### Scenario: Partially tagged collection
- **WHEN** some chunks carry the run's `embedding_model` and N chunks carry no tag
- **THEN** the run continues, logs a warning, and records `embedding_model_source = "chunks (N untagged)"` and `untagged_chunk_count = N`

#### Scenario: Tags agree with the run
- **WHEN** every tagged chunk carries the run's `embedding_model`
- **THEN** the run continues and records `embedding_model_source = "chunks"`

### Requirement: Comparison gates distinguish version, collection, and embedding differences
`compare_runs.py` SHALL refuse arms whose fingerprint version prefixes differ with a version reason before any corpus check, for the main arms and for every noise replicate; SHALL name both collections in the G3 reason when recorded collections differ; SHALL label `embedding_model` as the varied factor only when the fingerprints are equal and both arms record `embedding_model_source = "chunks"` with `untagged_chunk_count = 0`, and SHALL refuse the comparison when the embedding differs and either arm's provenance is unverified; and SHALL allow an unrecorded identity with a note when the embedding does not differ.

#### Scenario: v1 pin against v2 run
- **WHEN** one arm's fingerprint starts with `sha256:` and the other's with `sha256/v2:`
- **THEN** the comparison is refused, and the reason says the fingerprint versions differ and a re-pin is needed

#### Scenario: Different collections
- **WHEN** the arms' fingerprints differ and their recorded `collection` values differ
- **THEN** the G3 reason names both collections

#### Scenario: Embedding A/B
- **WHEN** the arms' fingerprints are equal, they differ only in `embedding_model`, and both record `embedding_model_source = "chunks"` with `untagged_chunk_count = 0`
- **THEN** the comparison runs, and the report header states the varied factor with both model identifiers

#### Scenario: Unverified embedding provenance
- **WHEN** the arms differ in `embedding_model` and one arm records `embedding_model_source` other than `"chunks"`, or `untagged_chunk_count > 0`, or no identity
- **THEN** the comparison is refused, the reason names that arm, and no flag admits it

#### Scenario: v1 noise replicate against a v2 baseline
- **WHEN** a noise replicate's fingerprint starts with `sha256:` and the baseline's with `sha256/v2:`
- **THEN** the comparison is refused with the version reason before the replicate corpus check, and `--corpus-differs-by-design` does not admit it

#### Scenario: Identity not recorded
- **WHEN** one arm has no `retrieval_identity`
- **THEN** the comparison runs, and the report notes that the identity of that arm is not recorded

#### Scenario: QA run answered from another corpus
- **WHEN** a `--qa-run` summary records corpus readings that are unavailable, that differ from each other, or whose version or digest differs from the joined arm's `corpus_fingerprint`
- **THEN** the QA run is refused at the gate exit code with a corpus reason, and a QA run that recorded no reading joins as before

### Requirement: Archive tooling accepts the versioned digest
`archive_run.sh` SHALL treat a `sha256/v2:` reading as usable, SHALL refuse a pin and an artifact whose prefixes differ with a version reason except on the closing-baseline re-pin (arm 00, fresh deploy, `--new-corpus`), which moves the pin to the new version and records the old pin, and the other readers of the digest SHALL accept it unchanged.

#### Scenario: Archive a v2 artifact
- **WHEN** `archive_run.sh` reads an artifact whose two readings start with `sha256/v2:` and are equal
- **THEN** it records the pin

#### Scenario: Pin and artifact versions differ
- **WHEN** the recorded pin starts with `sha256:` and the artifact's readings start with `sha256/v2:`
- **THEN** `archive_run.sh` refuses, and the reason says the fingerprint versions differ

#### Scenario: Closing baseline re-pins a v1 stack under v2
- **WHEN** the recorded pin starts with `sha256:`, arm 00 ran on a fresh deploy, and `archive_run.sh` archives its `sha256/v2:` artifact with `--new-corpus`
- **THEN** the pin becomes the v2 digest and the ledger row records the old pin in `repinned_from`

### Requirement: The backfill derives the identity from the recorded running configuration
The provenance backfill SHALL stamp `retrieval_identity` on every arm that carries `running_configuration`, label the stamp as reconstructed from it, and SHALL NOT overwrite an existing key or stamp an arm that has no `running_configuration`.

#### Scenario: Arm with running configuration
- **WHEN** the backfill reads an arm whose `running_configuration.data_manager` names a collection and an embedding
- **THEN** it writes `retrieval_identity` with `source = "reconstructed from running_configuration"`

#### Scenario: Report already version-stamped
- **WHEN** a report's metadata already has `code_version` or `config_versions` and an arm lacks `retrieval_identity`
- **THEN** the backfill stamps that arm and leaves the version keys unchanged

#### Scenario: Arm already stamped
- **WHEN** the backfill reads an arm that already has `retrieval_identity`
- **THEN** it leaves the arm unchanged

#### Scenario: Arm without running configuration
- **WHEN** the backfill reads an arm with no `running_configuration`
- **THEN** it writes no identity and reports the arm as skipped
