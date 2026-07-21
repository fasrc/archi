## ADDED Requirements

### Requirement: Configurable reranker backend

The cross-encoder rerank stage SHALL be pluggable behind a reranker seam so the reranker
backend is selectable via `data_manager.retrievers.hierarchical_rerank.reranker.backend`.
The system SHALL support at least a local `flashrank` backend (the default) and a managed
`bedrock` backend (Amazon Bedrock Rerank, e.g. `cohere.rerank-v3-5:0`). When `backend` is
unset, the system SHALL behave exactly as the local FlashRank reranker did before this change,
so an existing deployment renders and runs identically.

#### Scenario: Default backend is the local reranker

- **WHEN** the config omits `hierarchical_rerank.reranker.backend`
- **THEN** the hierarchical retriever reranks with the local FlashRank cross-encoder, producing
  the same ranking as before this change

#### Scenario: Bedrock backend selected by config

- **WHEN** `hierarchical_rerank.reranker.backend` is set to `bedrock` with a valid model id/ARN
- **THEN** the hierarchical retriever reranks the candidate pool by calling the Amazon Bedrock
  Rerank API and orders results by the returned relevance scores

#### Scenario: Backend swap does not change the retriever contract

- **WHEN** the reranker backend changes between `flashrank` and `bedrock`
- **THEN** the retriever still returns deduplicated parent-context `Document` objects through the
  same `search_vectorstore_hybrid` tool seam, with no agent or prompt changes

### Requirement: Reranker ranks the full candidate pool

A reranker backend SHALL return a relevance ordering over the **entire** candidate pool passed
to it, not a pre-truncated subset. Final top-N selection and parent deduplication SHALL happen
in the retriever after reranking. A managed backend that accepts a result-count parameter SHALL
request a count equal to the candidate-pool size.

#### Scenario: A low-ranked candidate is the first hit for a unique parent

- **WHEN** the highest-scoring candidates all map to already-seen parents and a lower-ranked
  candidate is the first hit for an otherwise-unseen parent
- **THEN** that parent still appears in the returned results, because the reranker ranked the
  full pool rather than truncating before parent deduplication

#### Scenario: Managed backend requests the full pool

- **WHEN** the `bedrock` backend reranks a pool of N candidates
- **THEN** it requests N results from the Rerank API (not the final top-N), so no candidate is
  dropped before parent mapping

### Requirement: Graceful fallback when a remote reranker fails

The system SHALL fall back to the local FlashRank reranker when a remote reranker backend fails
(error, timeout, or throttling) on a query, rather than failing retrieval, and SHALL NOT
hard-depend on a remote reranker being reachable.

#### Scenario: Remote rerank error degrades to local

- **WHEN** the `bedrock` backend is selected and a rerank call errors or times out
- **THEN** the retriever reranks that query with the local FlashRank reranker and returns
  results, logging the fallback

#### Scenario: Fallback path is available without a cold-start penalty at failure time

- **WHEN** the `bedrock` backend is the configured primary
- **THEN** the local fallback reranker is initialized ahead of first use, so a fallback does not
  incur the one-time model-load cost at the moment the remote backend is already failing
