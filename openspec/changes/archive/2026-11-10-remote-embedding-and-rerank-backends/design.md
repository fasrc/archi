## Context

archi builds its embedder from `data_manager.embedding_class_map` through
`ConfigService._resolve_embedding_classes`, which knows two classes: `OpenAIEmbeddings` and
`HuggingFaceEmbeddings`. Both run inside the chatbot, benchmark and data-manager containers,
whose PyTorch builds are CPU-only. The hierarchical retriever reranks its 20 child candidates
with FlashRank on CPU; it already accepts an injected ranker object with FlashRank's
`rerank(RerankRequest)` method so unit tests can stub it.

The trial models (Nemotron Embed/Rerank 1B, Qwen3-Embedding/Reranker-4B) need a GPU, and some
need their own prompts or downloaded code. They will run in a separate model server on the GPU
host; archi only needs to call it.

## Goals / Non-Goals

**Goals:**
- Call an OpenAI-compatible embeddings endpoint with distinct query and document models.
- Rerank through an HTTP endpoint selected by config, with no change to the retriever's ordering,
  dedupe or truncation logic.
- Leave every existing default, render and code path untouched when the new keys are absent.

**Non-Goals:**
- Shipping or managing the model server itself (it lives with the trial, outside archi).
- Changing the RAGAS judge's embedder, the retriever tool, or the agent loop.
- Making the chatbot image GPU-capable.

## Decisions

- **A small `RemoteEmbeddings` class instead of `OpenAIEmbeddings` with a `base_url`.**
  `langchain_openai.OpenAIEmbeddings` tokenizes input with tiktoken by default and sends token IDs,
  which a non-OpenAI server misreads; it also has one model name for queries and documents. A
  ~60-line `langchain_core` `Embeddings` subclass over `requests` sends plain text, supports
  `query_model`, batches documents, and is unit-testable with a patched `requests.post`.
- **Prompts live server-side, selected by model name** (`nemotron-embed:query` /
  `nemotron-embed:document`), rather than as client-side string prefixes. The server reuses the
  exact sentence-transformers prompt path the offline results came from, and archi needs only two
  model names.
- **`HttpRanker` mimics FlashRank's `Ranker.rerank`** and is injected by `build_vector_retriever`.
  The retriever already accepts an injected ranker, so its candidate ordering, parent dedupe and
  truncation stay byte-for-byte the same; only the scorer changes. The request body
  `{model, query, documents}` and response `results[{index, relevance_score}]` follow the
  Cohere/Jina/vLLM rerank shape.
- **Embedding failures raise.** A failed or malformed embedding call raises; there is no
  other embedder whose vectors would fit the same column.
- **Rerank failures fall back to FlashRank unless the config opts out.** Every chat turn
  searches at least once, so with a raising backend a reranker outage would fail most turns.
  `reranker.fallback` defaults to `flashrank`: the HTTP ranker is wrapped so that any failure
  logs a warning and reranks the same candidates with today's FlashRank model, loaded only on
  the first failure. `fallback: none` raises instead, which a benchmark arm testing a remote
  reranker should set so it can never quietly measure a different pipeline than its config
  names.
- **Template renders the new blocks only when configured**, using `tojson` for kwargs and URLs so
  types and special characters survive the YAML render.

## Risks / Trade-offs

- [The model server is down or slow] → reranking falls back to FlashRank with a warning, so
  answers keep coming at today's ranking quality; a hung server still costs up to `timeout`
  per search before the fallback runs. A down embedding server fails embedding, as any
  embedder outage does today.
- [A benchmark arm silently reranked by the fallback] → arms that test a remote reranker set
  `fallback: none`; every fallback also logs a warning naming the error.
- [Server-side prompts drift from what archi expects] → the trial's parity check compares server
  vectors and scores with the offline numbers before any ingest.
- [Vector size above pgvector's 2,000-dimension HNSW cap] → configs must set `dimensions` at or
  below 2,000; the server shortens Matryoshka vectors (e.g. to 1,024).

## Migration Plan

Opt-in only. A deployment adds `RemoteEmbeddings` to `embedding_class_map` (and sets
`embedding_name`) and/or sets `reranker.backend: http` with a `url`, then redeploys. Rollback is
removing those keys and redeploying; an embedder change re-embeds the corpus either way.

## Open Questions

- None blocking the trial. Whether to keep the HTTP backends after the trial depends on its result.
