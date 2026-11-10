## MODIFIED Requirements

### Requirement: Hierarchical retrieval with cross-encoder reranking

The system SHALL provide a retriever that generates candidates via the existing hybrid (BM25 + vector) child search, maps child hits to their parent nodes (deduplicating parents), reranks the candidates with a cross-encoder, and returns the top-ranked parent nodes as context. The cross-encoder SHALL be selected by `data_manager.retrievers.hierarchical_rerank.reranker.backend`: `flashrank` (the default when unset) runs the configured FlashRank model on CPU, and `http` sends the query and candidate texts to `{url}/rerank` and orders candidates by the returned scores.

#### Scenario: Child hit returns parent context

- **WHEN** a child node matches a query
- **THEN** the retriever returns the child's parent node text as the result context (not the bare child)

#### Scenario: Rerank narrows a larger candidate pool

- **WHEN** the retriever gathers a candidate pool larger than the final result count (e.g., ~20 candidates)
- **THEN** a cross-encoder reranks them and the retriever returns the top results (e.g., 5)

#### Scenario: Duplicate parents are merged

- **WHEN** multiple child hits share the same parent
- **THEN** that parent appears at most once in the returned results

#### Scenario: FlashRank remains the default backend

- **WHEN** a config sets no `reranker.backend`
- **THEN** the rendered config sets `backend: flashrank` and reranking uses the configured FlashRank model, as before

#### Scenario: HTTP backend orders candidates by server scores

- **WHEN** `reranker.backend` is `http` with a `url` and `model`
- **THEN** the retriever POSTs `{"model", "query", "documents"}` to `{url}/rerank` with the candidate child texts and orders candidates by each result's `relevance_score`, keeping the same parent dedupe and truncation

#### Scenario: HTTP backend failures fall back to FlashRank by default

- **WHEN** `reranker.backend` is `http`, `reranker.fallback` is unset or `flashrank`, and the rerank endpoint is unreachable, returns a non-2xx status, or omits scores for some candidates
- **THEN** the retriever logs a warning naming the error and reranks the same candidates with the default FlashRank model, so the search still returns results

#### Scenario: HTTP backend failures raise when the fallback is off

- **WHEN** `reranker.backend` is `http`, `reranker.fallback` is `none`, and the rerank request fails in any of those ways
- **THEN** retrieval raises an error instead of falling back to another ordering

#### Scenario: An unknown fallback is refused

- **WHEN** `reranker.fallback` is set to anything other than `flashrank` or `none`
- **THEN** building the retriever fails with an error naming `reranker.fallback`

#### Scenario: HTTP backend requires a URL

- **WHEN** `reranker.backend` is `http` and no `url` is set
- **THEN** building the retriever fails with an error naming the missing setting
