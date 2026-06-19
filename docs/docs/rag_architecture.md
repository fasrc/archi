# Archi RAG Architecture — Dev-Spike Handoff

A brief, opinionated map of archi's retrieval-augmented-generation pipeline,
written to orient someone prototyping **new RAG approaches**. It emphasizes the
extension seams and the current defaults you'd vary in a spike. Paths are
relative to the repo root; cite the listed files for ground truth.

## Pipeline at a glance

```
                 ingest                  index                    serve
 sources ──► collectors ──► chunk ──► embed ──► Postgres ──► retriever ──► agent ──► LLM
 (web/git/    (data_manager  (Character  (HF/OpenAI  (pgvector +   (hybrid:     (LangGraph   (vLLM:
  files/...)   collectors)   TextSplit)  embeddings) BM25 index)   vec+BM25)    ReAct loop)  Qwen 3.6)
```

Two processes: a **data-manager** (ingest → embed → store) and a **chat_app**
(retriever + agent + generation). They share one Postgres DB. Config is seeded
into Postgres at `archi create`; editing YAML + restarting is a no-op (re-run
the deploy). Code changes need a redeploy too — the app imports a baked copy of
`src/`, not the bind mount (dev mode is the exception).

## Stages

**1. Ingestion** — `src/data_manager/collectors/`. Sources: web links
(`scrapers/scraper_manager.py`), local files, git, Jira/Redmine tickets, Indico,
SSO. Web crawl depth defaults to **1** (`base_source_depth`), so the page→page
**link graph is extracted but discarded** — only visited within a crawl session,
never persisted (`scrapers/scraper.py`). HTML is flattened to text via
`BSHTMLLoader` (`loader_utils.py`); structure (headings, breadcrumbs, link
anchors) is lost. Per-doc metadata → `documents` table + a JSONB `extra_json`
catch-all (`src/cli/templates/init.sql`).

**2. Chunking & embedding** — `data_manager/manager.py`. Always
`CharacterTextSplitter`, `chunk_size=1000`, `chunk_overlap=0` (config defaults).
No semantic/markdown-aware splitting. Embeddings: HF
`sentence-transformers/all-MiniLM-L6-v2` (384-d) or OpenAI `text-embedding-3-small`
(1536-d). **Embedding dimension is fixed at deploy time** (`static_config`) — you
can't swap to a different-dim model without re-ingest.

**3. Storage** — Postgres + pgvector. `documents` (file-level) and
`document_chunks` (`chunk_text`, `embedding`, JSONB `metadata`). Chunk metadata
carries `filename`, `resource_hash`, `collection`, `chunk_index`, plus all parent
`extra_json` fields. Vector index: HNSW (m=16, ef=64). Full-text: pg_textsearch
BM25 index on `chunk_text` (GIN tsvector fallback). Distance: cosine
(`postgres_vectorstore.py`).

**4. Retrieval** — `data_manager/vectorstore/retrievers/`. Production default is
**HybridRetriever**: `semantic_weight=0.4`, `bm25_weight=0.6`, `k=5`. Score =
`(1 − cosine_distance)·0.4 + bm25·0.6` (`postgres_vectorstore.hybrid_search`).
`hybrid_search` **already accepts a `filter` dict** over any JSONB metadata key —
so metadata-scoped retrieval (e.g. by category/source_type) needs no schema
change, just a populated key + a caller that passes the filter. BM25-empty falls
back to semantic-only. No re-ranking stage exists.

**5. Agent & generation** — `src/archi/pipelines/agents/`. LangGraph ReAct loop
(`base_react.py`); production agent `CMSCompOpsAgent` (`cms_comp_ops_agent.py`).
Tools: `search_vectorstore_hybrid`, `search_metadata_index`,
`list_metadata_schema`, `search_local_files`, `fetch_catalog_document` (+ optional
MCP/MONIT). Two behaviors matter for RAG experiments:
- **Forced initial retrieval** (`force_initial_retrieval`, default on): a
  `search_vectorstore_hybrid` round is prefilled before the model's first turn,
  so retrieval always happens even if the model wouldn't call it. Toggle for
  prompt-only vs retrieval A/B.
- **Per-turn search budget** (default `search_vectorstore_hybrid: 2`): caps
  searches/turn; over-budget returns a synthetic "reuse prior results" message.

LLM: vLLM serving `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` at `localhost:8001/v1`.
Agent behavior (tool list, citation rules, first-action) is declared in a
**markdown spec** with YAML frontmatter under `config/agents/*.md`, live
bind-mounted; the active one is `dynamic_config.active_agent_name` (runtime
mutable). See `fasrc_archi.md` for the model-server ops.

## Knobs you'd vary in a spike

| Knob | Where | Default |
|------|-------|---------|
| chunk size / overlap | `data_manager.chunk_size/chunk_overlap` | 1000 / 0 |
| splitter | `manager.py` (hardcoded `CharacterTextSplitter`) | — |
| embedding model | `data_manager.embedding_name` (dim fixed at deploy) | MiniLM-L6 384-d |
| hybrid weights | `data_manager.retrievers.hybrid_retriever` | 0.4 / 0.6 |
| top-k | retriever / agent | 5 (tool returns 4, 800 chars each) |
| metadata filter | `hybrid_search(filter=...)` | unused by default |
| forced retrieval | `services.chat_app.force_initial_retrieval` | on |
| search budget | `tool_budgets.search_vectorstore_hybrid` | 2/turn |

## Extension seams (for new approaches)

- **New retriever**: subclass LangChain `BaseRetriever`, implement
  `_get_relevant_documents`, wrap with `create_retriever_tool()`
  (`tools/retriever.py`), register in `CMSCompOpsAgent._tool_definitions()`.
  `SemanticRetriever`/`GradingRetriever` are existing examples. A re-ranker or
  query-rewrite stage slots in here.
- **New tool** (e.g. graph hop, category filter, multi-query): `@tool` callable
  returning a formatted string; register in `_tool_definitions()`; add to the
  agent's markdown `tools:` frontmatter to enable.
- **New metadata** (e.g. category/taxonomy for steered retrieval): enrich
  `file_level_metadata` during ingest (`manager.py`) → lands in chunk JSONB →
  filterable via the existing `hybrid_search(filter=...)`. No schema change.
- **New data source**: implement the Collector protocol
  (`collectors/base.py`), persist via `persist_resource()`.
- **Capture the link graph** (currently discarded): the scrape point in
  `scrapers/scraper.py` already parses anchors; persisting them (JSONB or a
  relations table) is the seam for link-graph / GraphRAG experiments.

**Hardcoded (would need real changes):** Postgres-only vector backend; pgvector
metrics only; single `CharacterTextSplitter`; ReAct loop shape; BM25 via
pg_textsearch only.

## Known gaps / opportunities

- Link graph and HTML structure are thrown away at ingest → no structural or
  multi-hop retrieval today.
- No re-ranking; hybrid scores are a fixed linear blend.
- Category/taxonomy metadata exists as a concept but isn't populated or used to
  steer retrieval, even though the filter plumbing is ready.
- `chunk_overlap=0` and char-based splitting can sever context mid-thought.

## How to evaluate a change

RAGAS leaderboard sweep: `archi evaluate --config-dir <dir>` ranks variants on
faithfulness / context-precision / relevance (judge = Harvard HUIT Bedrock
Claude; see `docs/docs/benchmarking.md`). **Caveat:** the benchmark question set
is currently ~9 queries — too few to measure retrieval deltas with confidence.
**Expanding the question set (to ~40–60, incl. multi-hop and
category-confusable items) is a prerequisite** for trusting any RAG-change
numbers. Corpus is on the order of a few hundred docs / a few thousand chunks
(query `document_chunks` for the exact live count) — at this scale the problem is
usually precision/steering, not recall, which is worth keeping in mind when
choosing what to prototype.

## Key files

| Area | File |
|------|------|
| Scraping / link graph | `src/data_manager/collectors/scrapers/scraper.py` |
| Chunk + embed | `src/data_manager/manager.py` |
| Schema | `src/cli/templates/init.sql` |
| Vector store + hybrid search | `src/data_manager/vectorstore/postgres_vectorstore.py` |
| Retrievers | `src/data_manager/vectorstore/retrievers/` |
| Retriever tool factory | `src/archi/pipelines/agents/tools/retriever.py` |
| Agent base (ReAct, budgets) | `src/archi/pipelines/agents/base_react.py` |
| Production agent (forced retrieval, tools) | `src/archi/pipelines/agents/cms_comp_ops_agent.py` |
| Agent specs | `config/agents/*.md` |
| Config template | `src/cli/templates/base-config.yaml` |
| Benchmarking | `docs/docs/benchmarking.md` |
