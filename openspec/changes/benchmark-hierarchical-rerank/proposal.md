## Why

The hierarchical-rerank retriever (PR #31) shipped default-off and correctness-verified,
but its **value** was never measured — task 6.2 of `add-hierarchical-rerank-retrieval`
was deferred as its own effort (issue #32). We don't know whether structural
chunking + cross-encoder rerank (returning parent context) actually improves answer
quality over the `CharacterTextSplitter` + `HybridRetriever` baseline, what it costs
in latency, or how much it grows the deployment image — so we cannot make an informed
default-on/default-off decision or recommend tuning values. This change produces that
evidence with a reproducible A/B benchmark on the FASRC corpus.

## What Changes

- Add a reproducible **two-arm benchmark** (baseline vs hierarchical-rerank treatment)
  runnable via the existing `archi evaluate -cd` harness, holding everything fixed
  except chunking strategy + retriever. (Config pair + banks drafted this session under
  `examples/benchmarking/`.)
- Add two **FASRC question banks** in the harness schema: 27 real ServiceNow tickets
  (RAGAS-only) and 21 doc-grounded, typed questions (`easy_retrieve` / `reasoning` /
  `should_refuse`) for sliced analysis.
- Plumb **`parent_chunk_size` / `child_chunk_size` through `data_manager.chunking`**
  config (currently hardcoded 2048/512 in `node_parsing.py`) so chunk sizes are
  sweepable — without this, a "recommend default parent/child sizes" outcome cannot be
  data-driven. This is the only application-code change; it is gateable and not
  deploy-bound.
- Define and run the **measurement protocol**: RAGAS quality per arm (+ per question
  type), warm-vs-cold per-query latency (isolating the one-time FlashRank ONNX load),
  and the built-image size delta from `llama-index-core` + `flashrank`.
- Produce a **recommendation** (default-on/off, parent/child sizes, `bm25_weight`)
  grounded in the recorded numbers, captured as a decision record.

## Capabilities

### New Capabilities
- `retrieval-benchmarking`: a reproducible A/B benchmark for retrieval changes — typed
  FASRC question banks, a held-fixed baseline/treatment config pair, and a measurement
  protocol covering RAGAS quality, warm/cold latency, and image-size delta, yielding a
  data-grounded recommendation.

### Modified Capabilities
- `hierarchical-rerank-retrieval`: parent/child chunk sizes become configurable via
  `data_manager.chunking` (new optional `parent_chunk_size` / `child_chunk_size` keys)
  instead of hardcoded constants, so they can be tuned and swept.

## Impact

- **New artifacts** (data/config, no runtime code): `examples/benchmarking/snow_ragas_queries_pt1.json`,
  `examples/benchmarking/fasrc_ragas_queries.json`, `examples/benchmarking/hierarchical_rerank_ab/`.
- **Code (gateable):** `src/data_manager/vectorstore/node_parsing.py` (already accepts the
  params) + `src/data_manager/vectorstore/manager.py:795` call site to pass config-derived
  sizes; config read in the data-manager config path. New unit tests for the config plumbing.
- **Docs:** `docs/docs/benchmarking.md` (the A/B recipe) and a new decision record under
  `docs/decisions/` for the recommendation.
- **Deploy-bound:** the actual benchmark run requires the live dev deployment (vLLM SUT,
  HuggingFace embeddings, Bedrock judge) and a double corpus ingest — these tasks are
  marked `needs-deploy` and cannot be validated by the local gate.
- No change to the prod default (feature stays default-off until the recommendation lands).
