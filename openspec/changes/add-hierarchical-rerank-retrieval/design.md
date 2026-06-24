## Context

Archi's ingestion (`VectorStoreManager`, `vectorstore/manager.py`) splits LangChain `Document`s with `CharacterTextSplitter(chunk_size, chunk_overlap=0)` (`:82`), enumerates them into `chunk_index`, embeds with `sentence-transformers/all-MiniLM-L6-v2` (384-dim, CPU), and inserts into the flat `document_chunks` table (`id`, `document_id`, `chunk_index`, `chunk_text`, `embedding vector(384)`, `metadata JSONB`, `UNIQUE(document_id, chunk_index)`). Retrieval goes Agent → `create_retriever_tool` (`tools/retriever.py`) → `HybridRetriever` → `PostgresVectorStore.hybrid_search` (weighted cosine + BM25, requires a pg_textsearch BM25 index; `hybrid_search`'s signature defaults to 0.7/0.3 but archi's config — `base-config.yaml` `data_manager.retrievers.hybrid_retriever` — sets the effective default to `semantic_weight=0.4, bm25_weight=0.6`). The agent addresses retrieval only by the tool name `search_vectorstore_hybrid`; `create_retriever_tool._normalize_results` already accepts both `Document` and `(Document, score)`. The FASRC corpus is SSO-scraped MkDocs HTML via `sources.links`. `init.sql` is `CREATE TABLE IF NOT EXISTS` (create-only; no migration runner), so schema changes only take effect on a fresh volume.

## Goals / Non-Goals

**Goals**
- Structural, context-preserving chunking (parent-child) replacing fixed-character splitting.
- A reranking retriever (hybrid candidates → cross-encoder → top-5 parent context) behind the existing seam.
- Additive storage that leaves `document_chunks` and `main`'s retrieval byte-compatible.
- Zero change to the agent loop, prompts, or tool contract.
- Measurable retrieval-quality improvement on the FASRC question set; no latency regression.

**Non-Goals**
- No change to `document_chunks` schema or `PostgresVectorStore.hybrid_search` behavior.
- No GPU reranker (bge-reranker-large) — CPU only.
- No agent/prompt/tool-name changes.
- Not wiring CMSCompOpsAgent in this change (it can adopt later via the same one-line swap).
- No in-place data migration — re-ingest on a fresh volume.

## Decisions

**D1 — SentenceSplitter primary, MarkdownElementNodeParser opt-in.**
The FASRC corpus is scraped HTML, not raw markdown, so `MarkdownElementNodeParser` (which needs real markdown structure) would underperform as the default. `SentenceSplitter` preserves sentence/paragraph boundaries on any text. Markdown parsing becomes an opt-in path for git-sourced `.md`. *Alternative rejected:* markdown parser as default — brittle on HTML-derived text.

**D2 — Storage Option B: additive `document_parent_nodes` table.**
Children (embedded leaves) stay in `document_chunks` exactly as today. Parents live in a new table keyed independently, linked by `parent_id`. *Alternatives rejected:* (A) one table with `parent_chunk_id`/`node_level` — collides with `UNIQUE(document_id, chunk_index)` and leaks parents into the shared `hybrid_search` BM25/vector path, regressing `main`; (C) parent text in child `metadata` JSONB — zero migration but duplicates parent text per child and can't dedupe. B is the clean, main-safe home and mirrors LlamaIndex's docstore.

**D3 — Hybrid candidate generation, cross-encoder rerank.**
Reuse `hybrid_search` (BM25+vector) to fetch top ~20 child hits (lexical + semantic recall), then let FlashRank supply precision. *Alternative rejected:* vector-only candidates — loses BM25 lexical recall that the cross-encoder can't recover if the candidate never appears.

**D4 — FlashRank (ONNX, CPU) reranker.**
Embeddings already run on CPU; the dev box GPUs are claimed by the model server. FlashRank is ~MBs and CPU-fast. *Alternative rejected:* bge-reranker-large — needs torch/GPU and bloats the image.

**D5 — Return parents, rank on children.**
Vector hits are on precise child nodes; the returned context is the parent (auto-merge style). Dedupe parents so multiple child hits under one parent collapse to a single, larger context document.

**D6 — Force archi's embedder into LlamaIndex.**
Inject the existing `embedding_model` as LlamaIndex's embed model (or bypass LlamaIndex embedding entirely and embed leaves via archi's `embedding_model`). The query embeds with the same model at search. Guard with a dimension assertion (must be 384) so drift fails loudly, not silently.

**D7 — Integration via retriever swap only.**
Implement `LlamaIndexHierarchicalRetriever(BaseRetriever)` returning `List[Document]` (or `(Document, score)`), and swap it for `HybridRetriever` in `FASRCDocsAgent._update_vector_retrievers`. `create_retriever_tool` and the tool name are untouched.

## Risks / Trade-offs

- **Embedding drift (silent, severe)** → reuse archi's `embedding_model`; assert query/leaf dim == 384; unit-test the assertion.
- **Parent leakage into `main`'s read path** → Option B keeps parents in a separate, never-embedded, never-BM25-indexed table; add a test that `hybrid_search` results contain no parent rows.
- **Latency from reranking** → rerank only ~20 short candidates on CPU (tens of ms); expect net improvement via fewer agent search loops. Measure before/after; gate behind config so it can be disabled.
- **Dependency weight / image bloat** → `llama-index-core` (not the full `llama-index` meta-package) + `flashrank`; record image-size delta.
- **Re-ingest required** → chunking changes invalidate existing chunks; `delete_existing_collection_if_reset` + fresh volume on the spike. Acceptable; no production data at risk on the branch.
- **Two write paths** → upgrade the production path (`_add_to_postgres`); leave `PostgresVectorStore.add_texts` naive but documented (it won't produce parents).

## Migration Plan

1. Add deps (`llama-index-core`, `flashrank`) on the branch.
2. Add `document_parent_nodes` DDL to `init.sql` (additive, `IF NOT EXISTS`).
3. Implement node parsing + leaf/parent persistence in the ingestion path behind a config flag.
4. Implement `LlamaIndexHierarchicalRetriever` (+ FlashRank), config-gated.
5. Swap the retriever in `FASRCDocsAgent`.
6. Recreate the dev volume, re-ingest, smoke-test a chat turn end-to-end.
7. Evaluate retrieval quality on the FASRC question set; record latency + image-size deltas.
   *Rollback:* config flags off → falls back to `CharacterTextSplitter` + `HybridRetriever`; the new table is inert.

## Open Questions

- Optimal parent/child sizes for MkDocs-derived content (tune during eval).
- Whether to keep BM25 weight as-is for candidate gen once the cross-encoder is in front (eval may favor higher recall / lower bm25_weight).
- FlashRank model choice (e.g., `ms-marco-MiniLM-L-12-v2` vs smaller) — pick on the quality/latency curve during eval.

## Review-driven hardening (Codex adversarial review)

A branch-level adversarial review surfaced two correctness gaps in the initial
implementation, addressed by tasks 1.5 and 2.5:

- **Schema lifecycle (task 1.5).** `init.sql` runs only when Postgres initializes a
  *fresh* data directory, and archi has no migration runner — so a deployment that
  enables the feature on an existing volume would hit an undefined-table error when
  ingestion/retrieval touch `document_parent_nodes`. archi already uses runtime
  `CREATE TABLE IF NOT EXISTS` ensures (`collectors/utils/index_utils.py`); the
  feature follows that pattern to ensure its table/index before use. `init.sql`
  keeps the DDL for fresh volumes; the runtime ensure covers upgrades.
- **Embedding dimension coupling (task 2.5).** The chunk vector column is
  deploy-time configurable (`init.sql` `embedding vector({{ embedding_dimensions }})`,
  backed by `static_config.embedding_dimensions`), and the default `embedding_name`
  is `OpenAIEmbeddings` (1536-dim). The first cut hardcoded a 384-dim child guard,
  which would fail every file on a non-MiniLM deployment. The guard must derive the
  expected dimension from the configured `embedding_dimensions` so it matches the
  column for any backend.
