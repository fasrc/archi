## ADDED Requirements

### Requirement: Inline citation block
The system SHALL append a formatted citation block to every response that includes source documents. The block SHALL appear after the answer text, separated by a markdown horizontal rule.

#### Scenario: Response with sources
- **WHEN** a pipeline returns a response with 3 source documents
- **THEN** the response text ends with `\n\n---\n**Sources:**\n- \`filename1.md\` (relevance: 0.92)\n- \`filename2.md\` (relevance: 0.87)\n- \`filename3.md\` (relevance: 0.81)`

#### Scenario: Response with no sources
- **WHEN** a pipeline returns a response with an empty `source_documents` list
- **THEN** no citation block is appended to the response

---

### Requirement: Source deduplication
The system SHALL deduplicate sources by filename. When multiple chunks from the same document are retrieved, only the highest-scoring occurrence SHALL appear in the citation block.

#### Scenario: Duplicate document chunks
- **WHEN** a pipeline returns 5 source documents where 3 are chunks from `guide.md` (scores 0.95, 0.88, 0.82) and 2 are from `faq.md` (scores 0.90, 0.85)
- **THEN** the citation block lists `guide.md` (relevance: 0.95) and `faq.md` (relevance: 0.90) — two entries, not five

---

### Requirement: Collection labeling for multi-collection queries
When sources originate from multiple collections (via collection groups), the citation block SHALL label each source with its collection name in brackets.

#### Scenario: Cross-collection sources
- **WHEN** a collection group query returns sources from both "cluster-docs" and "runbooks" collections
- **THEN** sources are labeled: `\`failover.md\` [runbooks] (relevance: 0.92)` and `\`ha-overview.md\` [cluster-docs] (relevance: 0.85)`

#### Scenario: Single collection sources
- **WHEN** all sources originate from the same collection
- **THEN** no collection label is shown (labels add no information)

---

### Requirement: Shared utility
The citation formatter SHALL be a standalone utility usable by both the `/v1` endpoint and archi's native chat response path. It SHALL NOT be coupled to any specific response format.

#### Scenario: Used by /v1 endpoint
- **WHEN** the `/v1` translator builds the final SSE chunk
- **THEN** it calls the citation formatter with the source documents and appends the result to the content

#### Scenario: Used by native endpoint
- **WHEN** the native NDJSON streaming endpoint builds the final response
- **THEN** it can call the same citation formatter to append sources to the answer text
