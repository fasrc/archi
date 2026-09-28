## ADDED Requirements

### Requirement: Every evaluation run records its retrieval identity
Every evaluation run SHALL record the collection tag it searched, the embedding class name, and the embedding model identifier, derived from the run's configuration by one shared helper that uses the same collection formula as `VectorstoreConnector`.

#### Scenario: Golden-set arm records the identity
- **WHEN** the golden-set harness finishes an arm
- **THEN** the arm's record contains `retrieval_identity` with `collection`, `embedding_name`, `embedding_model`, `chunk_count`, `usable_chunk_count`, and `embedding_model_source`

#### Scenario: QA run records the identity
- **WHEN** the QA workflow starts a run whose agent spec uses the vectorstore search tool
- **THEN** `manifest.json` contains `retrieval_identity` with the same fields

#### Scenario: Embedding model comes from the class-specific kwarg
- **WHEN** the config's `embedding_name` is `HuggingFaceEmbeddings` with `kwargs.model_name` set, or `OpenAIEmbeddings` with `kwargs.model` set
- **THEN** `embedding_model` equals that kwarg value, and when neither kwarg exists it equals the class name

### Requirement: The corpus fingerprint covers the searched collection and only rows retrieval can reach
The corpus fingerprint SHALL hash only chunks that the searched collection's retrieval filter admits, only parent nodes that such a chunk references, and only live documents that own such a chunk; it SHALL include each chunk's `collection` tag, `embedding_model` tag, and null-vector flag in the chunk digest, and each document's URL, display name, source type, and title in the document digest, and it SHALL NOT include any other `extra_json` field.

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

#### Scenario: Category-only change is not a corpus change
- **WHEN** only `extra_json->>'category'` changes on a document
- **THEN** the fingerprint is unchanged

#### Scenario: A vector set to NULL is covered
- **WHEN** one in-scope chunk's `embedding` becomes `NULL` and nothing else changes
- **THEN** the fingerprint changes

#### Scenario: Digest carries a version prefix
- **WHEN** the fingerprint is computed
- **THEN** the value starts with `sha256/v2:`

### Requirement: One routine computes the fingerprint for every consumer
The golden-set harness, the QA workflow, the feature-matrix sweep, and the category census SHALL compute the corpus fingerprint through one function in `src/utils/benchmark_provenance.py`, and no consumer SHALL read the query text out of another module's source.

#### Scenario: Sweep and harness agree by construction
- **WHEN** the feature-matrix pin check and the harness compute the fingerprint of one unchanged stack
- **THEN** the two digests are equal

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

#### Scenario: Tags agree with the run
- **WHEN** every tagged chunk carries the run's `embedding_model`
- **THEN** the run continues and records `embedding_model_source = "chunks"`

### Requirement: Comparison gates distinguish version, collection, and embedding differences
`compare_runs.py` SHALL refuse arms whose fingerprint version prefixes differ with a version reason before any corpus check, SHALL name both collections in the G3 reason when recorded collections differ, SHALL allow arms whose `embedding_model` differs and label it as the varied factor, and SHALL allow an unrecorded identity with a note.

#### Scenario: v1 pin against v2 run
- **WHEN** one arm's fingerprint starts with `sha256:` and the other's with `sha256/v2:`
- **THEN** the comparison is refused, and the reason says the fingerprint versions differ and a re-pin is needed

#### Scenario: Different collections
- **WHEN** the arms' fingerprints differ and their recorded `collection` values differ
- **THEN** the G3 reason names both collections

#### Scenario: Embedding A/B
- **WHEN** the arms differ only in `embedding_model`
- **THEN** the comparison runs, and the report header states the varied factor with both model identifiers

#### Scenario: Identity not recorded
- **WHEN** one arm has no `retrieval_identity`
- **THEN** the comparison runs, and the report notes that the identity of that arm is not recorded

### Requirement: The backfill derives the identity from the recorded running configuration
The provenance backfill SHALL stamp `retrieval_identity` on every arm that carries `running_configuration`, label the stamp as reconstructed from it, and SHALL NOT overwrite an existing key or stamp an arm that has no `running_configuration`.

#### Scenario: Arm with running configuration
- **WHEN** the backfill reads an arm whose `running_configuration.data_manager` names a collection and an embedding
- **THEN** it writes `retrieval_identity` with `source = "reconstructed from running_configuration"`

#### Scenario: Arm already stamped
- **WHEN** the backfill reads an arm that already has `retrieval_identity`
- **THEN** it leaves the arm unchanged

#### Scenario: Arm without running configuration
- **WHEN** the backfill reads an arm with no `running_configuration`
- **THEN** it writes no identity and reports the arm as skipped
