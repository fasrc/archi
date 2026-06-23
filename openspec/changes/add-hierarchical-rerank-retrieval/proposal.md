## Why

Archi's retrieval is naive: `CharacterTextSplitter(chunk_overlap=0)` slices documents on a fixed character count with no structural awareness, fragmenting sentences and tables and severing context between adjacent chunks. The agent compensates by looping `search_vectorstore_hybrid` 8–50× per question (latency ~125s, often truncated) because no single retrieval surfaces a complete, well-scoped answer. The two highest-ROI levers are **better chunks** (so each retrieval is precise and self-contained) and **better ranking** (so the top results are actually the most relevant). This is the "Data-First 1-2 Punch": structural parent-child chunking + cross-encoder reranking, delivered behind the existing retriever seam so the agent, prompts, and tool contract do not change.

## What Changes

- **Ingestion:** Replace `CharacterTextSplitter` (`vectorstore/manager.py:82`) with a LlamaIndex structural parser. Primary = `SentenceSplitter` (robust on the SSO-scraped MkDocs HTML that makes up the FASRC corpus); `MarkdownElementNodeParser` is an opt-in path for genuinely-markdown git sources.
- **Parent-child chunking:** Produce small embedded **child/leaf** nodes (high-precision vector search) linked to larger **parent** nodes (context continuity).
- **Storage (additive):** Add a new `document_parent_nodes` table for parent text + child→parent links. `document_chunks` is unchanged — it continues to hold only embedded leaves, so the `main` schema and its `hybrid_search` read path stay byte-compatible.
- **Retrieval + rerank:** New `LlamaIndexHierarchicalRetriever` (a `langchain_core` `BaseRetriever`): reuse the existing hybrid (BM25+vector) search for top ~20 child candidates → map children to parents (dedupe/merge) → rerank with a CPU cross-encoder (**FlashRank**, ONNX) → return top 5 **parent** nodes as LangChain `Document`s.
- **Integration (one-line swap):** `create_retriever_tool` already normalizes `Document` and `(Document, score)`; the only wiring change is swapping `HybridRetriever` for the new retriever inside `FASRCDocsAgent._update_vector_retrievers` (`fasrc_docs_agent.py:183/197`). Agent loop, prompts, and the tool name `search_vectorstore_hybrid` are unchanged. CMS agent can adopt via the identical swap.
- **Embedding consistency:** Force LlamaIndex to use archi's existing embedder (`sentence-transformers/all-MiniLM-L6-v2`, 384-dim, CPU). LlamaIndex must never embed with its own default.
- **NOT changing:** the agent ReAct loop, prompt templates, tool name/signature, the `document_chunks` schema, or `PostgresVectorStore.hybrid_search`'s existing behavior.

## Capabilities

### New Capabilities
- `hierarchical-rerank-retrieval`: structural parent-child ingestion, additive parent-node storage, and a hybrid-candidate → cross-encoder-rerank retriever that returns parent-context documents through the existing LangChain retriever/tool seam.

### Modified Capabilities
<!-- None at the spec level: the agent/tool contract and the document_chunks schema are intentionally preserved. The retriever swap is an implementation detail behind create_retriever_tool. -->

## Impact

- **New code:** LlamaIndex node-parsing in the ingestion path; `document_parent_nodes` DDL in `init.sql`; `LlamaIndexHierarchicalRetriever`; a FlashRank rerank step.
- **Changed code:** `vectorstore/manager.py` (splitter + leaf/parent persistence in `_add_to_postgres`); one-line retriever swap in `fasrc_docs_agent.py`.
- **Untouched:** `document_chunks` schema, `PostgresVectorStore.hybrid_search`, `create_retriever_tool`, agent loop/prompts, `CMSCompOpsAgent` (opt-in).
- **Dependencies:** add `llama-index-core` + `flashrank` (CPU/ONNX); isolated to this branch; watch chatbot/data-manager image size.
- **Operational:** requires a full corpus re-ingest on a fresh volume (chunking changes; `init.sql` is create-only with no migration runner).
- **Scope:** fasrc/archi `spike/eval-llamaindex` branch; production target is FASRCDocsAgent.
