## ADDED Requirements

### Requirement: Remote embedding class
The system SHALL provide an embedding class `RemoteEmbeddings`, selectable through
`data_manager.embedding_name` and `data_manager.embedding_class_map`, that embeds text by POSTing
`{"model": <name>, "input": [<texts>]}` to `{base_url}/embeddings` and returns the vectors from the
response's `data` list in input order.

#### Scenario: Documents are embedded with the document model
- **WHEN** the vector store embeds a batch of chunk texts with `RemoteEmbeddings` configured with
  `model: m-doc` and `query_model: m-query`
- **THEN** the request names `m-doc` and sends the chunk texts unchanged

#### Scenario: Queries are embedded with the query model
- **WHEN** retrieval embeds a search query
- **THEN** the request names `m-query`, or `model` when no `query_model` is set

#### Scenario: Response order is restored
- **WHEN** the server returns the `data` items out of order
- **THEN** the vectors are returned in input order, using each item's `index`

#### Scenario: Large document batches are split
- **WHEN** more texts are embedded than `batch_size`
- **THEN** they are sent in consecutive requests of at most `batch_size` texts and the results are
  concatenated in order

#### Scenario: Failures raise
- **WHEN** the endpoint returns a non-2xx status or a response whose `data` count differs from
  the inputs
- **THEN** embedding raises an error rather than returning partial or placeholder vectors

### Requirement: Remote embedder configuration renders only when set
The base config template SHALL render a `RemoteEmbeddings` entry in `embedding_class_map`, with its
`kwargs` and `dimensions`, only when the source config defines one, and SHALL leave the existing
`OpenAIEmbeddings` and `HuggingFaceEmbeddings` entries unchanged.

#### Scenario: Configured remote embedder is rendered
- **WHEN** a source config sets `embedding_class_map.RemoteEmbeddings` with `kwargs` (base_url,
  model, query_model) and `dimensions: 1024`
- **THEN** the rendered config carries those kwargs with their types and `dimensions: 1024`, and
  the vector column is sized from that value

#### Scenario: Default render is unchanged
- **WHEN** a source config does not mention `RemoteEmbeddings`
- **THEN** the rendered `embedding_class_map` has no `RemoteEmbeddings` entry
