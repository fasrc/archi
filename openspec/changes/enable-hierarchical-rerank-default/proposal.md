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
- Update the template comment that currently says "Disabled by default" to reflect the new
  default and how to opt out (`enabled: false`).
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

- **Code:** `src/cli/templates/base-config.yaml` (one Jinja default + its comment). The Python
  runtime fallback in `src/data_manager/vectorstore/retrievers/factory.py`
  (`.get("enabled", False)`) is intentionally left conservative — see design.md.
- **Behavior:** new/re-rendered deployments retrieve via the hierarchical reranker by default;
  first query after each (re)deploy pays a one-time ~50 s FlashRank ONNX load.
- **Docs:** ADR 0003 already records the rationale; no further doc change required beyond the
  template comment.
- **Tests:** template-rendering unit coverage asserting the new default and the explicit
  opt-out.
