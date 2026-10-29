## 1. Remote embeddings

- [x] 1.1 Failing tests for `RemoteEmbeddings`: document vs query model, index-order restore, batching, non-2xx and count-mismatch errors
- [x] 1.2 Implement `src/data_manager/vectorstore/remote_embeddings.py`
- [x] 1.3 Failing test that `ConfigService._resolve_embedding_classes` maps `RemoteEmbeddings`; register it
- [x] 1.4 Failing render tests: `RemoteEmbeddings` entry rendered with typed kwargs and `dimensions` only when configured; existing entries unchanged
- [x] 1.5 Render the entry in `src/cli/templates/base-config.yaml`

## 2. HTTP reranker backend

- [x] 2.1 Failing tests for `HttpRanker.rerank`: request shape, score mapping back to passage ids, sort order, non-2xx and missing-score errors
- [x] 2.2 Implement `src/data_manager/vectorstore/retrievers/http_ranker.py`
- [x] 2.3 Failing factory tests: `backend: http` injects an `HttpRanker` with url/model; missing url raises; unset backend keeps FlashRank
- [x] 2.4 Wire `build_vector_retriever`
- [x] 2.5 Failing render tests: `backend` defaults to `flashrank`; `url` rendered only when set
- [x] 2.6 Render `backend` and `url` in `base-config.yaml`

## 3. Verify

- [x] 3.1 `bash scripts/gate.sh` green (format, unit tests, diff coverage >= 80%)
- [x] 3.2 `openspec validate remote-embedding-and-rerank-backends --strict`
