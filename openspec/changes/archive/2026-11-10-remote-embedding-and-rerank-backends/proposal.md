## Why

An offline replica of dev's retrieval (frozen dev corpus, 106 golden questions with an expected page)
found that the reranker, not the embedder, is the lever: swapping FlashRank for NVIDIA's
Nemotron Rerank 1B or Qwen3-Reranker-4B lifts page-found from 89 to 96, and stacking a stronger
embedder with vector weight 0.7 reaches 99 to 102. Those models need a GPU and in two cases their
own prompts or code, while archi can only load embedders in-process (sentence-transformers or the
OpenAI API) and rerank with FlashRank on CPU. To confirm the result with full golden-set runs, archi
must be able to call embedding and reranking models hosted on a GPU server.

## What Changes

- New embedding class `RemoteEmbeddings`: posts text to an OpenAI-compatible
  `{base_url}/embeddings` endpoint, with a separate model name for queries and for documents
  (the server applies each model's own prompt), explicit `dimensions`, and no client-side
  tokenization. Registered alongside `OpenAIEmbeddings` and `HuggingFaceEmbeddings`.
- `base-config.yaml` renders a `RemoteEmbeddings` entry in `embedding_class_map` only when the
  source config defines one; existing entries render exactly as before.
- New reranker backend setting `data_manager.retrievers.hierarchical_rerank.reranker.backend`:
  `flashrank` (default, unchanged) or `http`, with `url`. The `http` backend posts the query and
  the candidate texts to `{url}/rerank` and orders candidates by the returned scores. A failed
  request falls back to today's FlashRank model with a warning (`reranker.fallback: flashrank`,
  the default), or raises when `reranker.fallback` is `none`.
- Defaults are unchanged, so deployments that set none of the new keys render and behave as today.

## Capabilities

### New Capabilities
- `remote-embedding-backend`: embedding text through an OpenAI-compatible HTTP endpoint with
  separate query and document models and an explicit vector size.

### Modified Capabilities
- `hierarchical-rerank-retrieval`: the rerank stage gains a configurable backend; FlashRank on CPU
  stays the default, and an HTTP reranker can be selected by config.

## Impact

- Code: new `src/data_manager/vectorstore/remote_embeddings.py` and
  `src/data_manager/vectorstore/retrievers/http_ranker.py`; `ConfigService._resolve_embedding_classes`;
  `build_vector_retriever` in `retrievers/factory.py`; `src/cli/templates/base-config.yaml`.
- No new dependencies (`requests` is already pinned). No schema change: the vector column size
  still follows the configured `dimensions`.
- Operational: a deployment that opts in depends on the model server being reachable from the
  chatbot, benchmark and data-manager containers.
