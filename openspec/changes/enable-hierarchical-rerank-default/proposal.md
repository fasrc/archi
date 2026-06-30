## Why

The hierarchical-rerank retriever shipped **default-off** so it could be evaluated
without changing existing deployments. The issue #32 A/B benchmark is now complete:
hierarchical-rerank improves answer quality by **+0.108 mean RAGAS (+19%)** (context_recall
+0.206, context_precision +0.161) for **+2.0 s/q (+10%)** warm latency and negligible image
size. ADR `docs/decisions/0003-hierarchical-rerank-default-on.md` records the recommendation
to make it the default; this change executes that recommendation so new deployments get the
better retriever without per-deployment config.

## What Changes

- Flip the **shipped (rendered) default** for
  `data_manager.retrievers.hierarchical_rerank.enabled` from `false` to `true` in the CLI
  config template (`src/cli/templates/base-config.yaml`), so a deployment that does not set
  the key renders `enabled: true`.
- Flip the **paired default** `data_manager.chunking.strategy` from `character` to `sentence`,
  so the default-on reranker actually has parent/child nodes to expand. The reranker only
  returns parent context when ingestion built hierarchical nodes (`sentence`/`markdown`); with
  the legacy `character` strategy it pays the FlashRank cost over flat chunks and returns no
  parent context — i.e. not the benchmarked ADR 0003 package. The two defaults flip together
  so "default-on" means the configuration that produced the +19%.
- Update the template comments (chunking strategy + reranker) to reflect the new defaults and
  how to opt out (`enabled: false` / `strategy: character`).
- Operators keep full control: an explicit `enabled: false` in a deployment's config still
  renders `false` and falls back to `HybridRetriever` (existing behavior, unchanged).
- **Not breaking:** existing deployments carry their own already-rendered `config.yaml`; this
  only affects configs rendered *after* the change (new deployments and re-renders). No
  schema, API, or tool-contract change.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `hierarchical-rerank-retrieval`: the default configuration state changes from disabled to
  enabled — a freshly rendered deployment config SHALL enable hierarchical-rerank retrieval
  by default, while still allowing an explicit opt-out via `enabled: false`.

## Impact

- **Code:** `src/cli/templates/base-config.yaml` (two Jinja defaults — `chunking.strategy` and
  `hierarchical_rerank.enabled` — plus their comments). The Python runtime fallbacks
  (`factory.py` `.get("enabled", False)`; `manager.py` `chunking_cfg.get("strategy",
  "character")`) are intentionally left conservative — see design.md.
- **Behavior:** new/re-rendered deployments ingest with hierarchical (`sentence`) chunking and
  retrieve via the hierarchical reranker by default; ingestion now also populates
  `document_parent_nodes`, and the first query after each (re)deploy pays a one-time ~50 s
  FlashRank ONNX load.
- **Docs:** ADR 0003 already records the rationale; no further doc change required beyond the
  template comment.
- **Tests:** template-rendering unit coverage asserting the new default and the explicit
  opt-out.
