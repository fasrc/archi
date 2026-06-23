## 1. Dependencies & storage (additive)

- [x] 1.1 Add `llama-index-core` and `flashrank` to project dependencies (branch-scoped); record chatbot/data-manager image-size delta.
- [x] 1.2 Add `document_parent_nodes` DDL to `src/cli/templates/init.sql` (additive, `CREATE TABLE IF NOT EXISTS`): `id`, `document_id` FK→documents ON DELETE CASCADE, `parent_index`, `parent_text`, `metadata JSONB`; no `embedding` column; index on `document_id`. Leave `document_chunks` untouched.
- [x] 1.3 Add a child→parent link: store `parent_id` on the child row's existing `document_chunks.metadata` JSONB (no new column on document_chunks) referencing `document_parent_nodes.id`.
- [x] 1.4 Add config flags (default off): `data_manager.chunking.strategy` (`character`|`sentence`|`markdown`) and `data_manager.retrievers.hierarchical_rerank.enabled` + `reranker` settings (alongside the existing `data_manager.retrievers.*` entries).

## 2. Ingestion: structural parent-child chunking

- [x] 2.1 Add a LlamaIndex node-parsing helper that converts a LangChain `Document` → LlamaIndex `Document` → hierarchical nodes (parents + children), defaulting to `SentenceSplitter`, with `MarkdownElementNodeParser` selected for markdown sources.
- [x] 2.2 Force archi's embedder: embed child nodes with the existing `embedding_model` (do NOT use any LlamaIndex default embedder); assert each child embedding is 384-dim and fail loudly on mismatch.
- [x] 2.3 In `VectorStoreManager._add_to_postgres` (`vectorstore/manager.py`), when the structural strategy is enabled: persist parents to `document_parent_nodes`, persist children to `document_chunks` (existing insert path, embeddings + `metadata.parent_id`). Keep the `CharacterTextSplitter` path intact for fallback.
- [x] 2.4 Leave `PostgresVectorStore.add_texts` (LangChain-API write path) naive; add a comment noting it does not produce parent nodes.

## 3. Retrieval: hierarchical retriever + rerank

- [x] 3.1 Implement `LlamaIndexHierarchicalRetriever(BaseRetriever)` in `src/data_manager/vectorstore/retrievers/`: generate ~20 child candidates via `PostgresVectorStore.hybrid_search`, look up parents by `metadata.parent_id` from `document_parent_nodes`, dedupe parents.
- [x] 3.2 Add a FlashRank cross-encoder rerank step over the candidate pool; return top 5 parent nodes as LangChain `Document`s (optionally `(Document, score)`).
- [x] 3.3 Export the retriever from `retrievers/__init__.py`; gate it behind `data_manager.retrievers.hierarchical_rerank.enabled` with fallback to `HybridRetriever`.

## 4. Integration (single seam)

- [x] 4.1 In `FASRCDocsAgent._update_vector_retrievers` (`fasrc_docs_agent.py:183/197`), construct the new retriever when enabled and pass it to `create_retriever_tool` unchanged (tool name stays `search_vectorstore_hybrid`). Do not modify the agent loop, prompts, or `create_retriever_tool`.

## 5. Tests

- [x] 5.1 Unit: ingestion produces ≥1 child per parent and each child carries a valid `parent_id`.
- [x] 5.2 Unit: child embedding dimension assertion raises on mismatch; query and child use the same configured model.
- [x] 5.3 Unit: `hybrid_search` results never include parent rows (parents not embedded / not in document_chunks).
- [ ] 5.4 Unit: retriever maps child→parent, dedupes parents, and returns ≤5 parent Documents after rerank; reranker reorders a known pool.
- [ ] 5.5 Unit: with the feature disabled, retrieval falls back to `HybridRetriever`; agent still exposes `search_vectorstore_hybrid`.
- [ ] 5.6 Run `pytest tests/unit/` (in-container) and `isort`/`black` on changed files.

## 6. Evaluate & verify (spike)

- [ ] 6.1 Recreate the dev volume, re-ingest the FASRC corpus with structural chunking, smoke-test a chat turn end-to-end (HTTP 200, sources populated, parent context returned).
- [ ] 6.2 Measure retrieval quality on the FASRC question set (vs. `CharacterTextSplitter`+`HybridRetriever` baseline) and record latency + image-size deltas; note interaction with the open "cap search_vectorstore_hybrid calls" ticket.
- [ ] 6.3 Confirm `main`-compatibility: `document_chunks` schema unchanged and existing hybrid retrieval behavior intact with the feature disabled.
