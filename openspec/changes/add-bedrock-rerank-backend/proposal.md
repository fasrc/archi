## Why

archi's retrieval rerank stage is hardwired to a single CPU cross-encoder —
FlashRank `ms-marco-MiniLM-L-12-v2`, a small, 512-token, English-only, several-year-old
model. A managed reranker (Cohere Rerank 3.5 on Amazon Bedrock) may lift retrieval
quality meaningfully, but today there is **no seam to swap the reranker backend** —
the config `reranker.model` string only ever reaches `flashrank.Ranker(...)`, so it
can pick a different *FlashRank* model but not a different *backend*, and there is no
way to A/B one reranker against another. This change adds that seam and a Bedrock
adapter behind it, gated so the current behavior is the default, and structured
**spike-first**: prove the quality delta on the existing A/B harness before anything
becomes a production default.

## What Changes

- **New `Reranker` seam.** Introduce a small protocol
  (`rerank(query, passages) -> list[(index, score)]`, sorted descending, over the
  **full** candidate pool) inside the hierarchical retriever. Extract today's inline
  FlashRank call (`hierarchical_retriever.py:_rerank`) behind a `FlashRankReranker`
  adapter — no behavior change.
- **New `BedrockReranker` adapter (raw boto3).** Calls `bedrock-agent-runtime.rerank`
  with `cohere.rerank-v3-5:0`, mapping the response's `index` + `relevanceScore`
  directly onto the seam contract. `numberOfResults` is set to the full candidate-pool
  size (not the final top-N) so parent-dedup is never starved.
- **New `FallbackReranker` wrapper.** Remote primary → local FlashRank on any
  error/timeout, so a remote reranker never becomes a hard dependency on the answer path.
- **Config backend selector.** Add `data_manager.retrievers.hierarchical_rerank.reranker.backend`
  (`flashrank` default | `bedrock`) plus Bedrock sub-keys (`model` ARN/id, `region`,
  `timeout_s`, `fallback`). `factory.build_vector_retriever` constructs the selected
  backend. Default render is unchanged.
- **Dependency + deploy.** Add `boto3` to `pyproject.toml` `dependencies` (the deploy
  image does `pip install .`); this is archi's first AWS-SDK / SigV4 dependency.
- **Spike-first A/B.** Add a Bedrock arm to the existing
  `examples/benchmarking/hierarchical_rerank_ab/` harness to measure the RAGAS delta
  vs the FlashRank baseline **before** productionizing (credential plumbing, deploy,
  default flip are out of scope until the delta justifies them).
- **No default behavior change.** FlashRank stays the default backend; every existing
  deployment renders and runs byte-for-byte as before.

## Capabilities

### New Capabilities
<!-- none — this extends the existing rerank capability -->

### Modified Capabilities
- `hierarchical-rerank-retrieval`: the cross-encoder rerank stage becomes
  **backend-configurable** (local FlashRank or a managed Bedrock reranker) behind a
  reranker seam, with **graceful fallback** to the local reranker on remote failure and
  a **full-candidate-pool ranking** guarantee so parent deduplication is not starved.

## Impact

- **Code:** `src/data_manager/vectorstore/retrievers/hierarchical_retriever.py`
  (`_rerank` → seam), `.../retrievers/factory.py` (backend selection), a new reranker
  adapters module, `src/cli/templates/base-config.yaml` (`reranker.backend` keys).
- **Dependencies:** `+boto3` in `pyproject.toml` `dependencies` (mirror version in
  `requirements/` if pinned there); archi's first boto3/SigV4 usage.
- **Infra / secrets (productionization only):** AWS credentials injected into the
  container the same way `HUIT_API_KEY` is (`~/.secrets/` → env, no EC2 instance role
  on the on-prem FASRC box); Model Access enabled for `cohere.rerank-v3-5:0` and the
  `bedrock:Rerank` IAM action granted; region `us-east-1` recommended (lowest RTT from
  FASRC, Cohere 3.5 supported).
- **Data egress (governance):** query text + KB child chunks leave the box to AWS.
  The KB is public FASRC docs (low risk); user queries may carry PII — needs sign-off,
  since HUIT's proxy exists precisely to keep traffic in-boundary.
- **Deploy:** the container runs a non-editable install, so this needs a **redeploy**
  to take effect. Cost is negligible ($2 / 1,000 queries; one archi retrieval = one
  query).
- **Docs:** update `docs/docs/configuration.md` (new `reranker.backend` keys) and
  `docs/docs/benchmarking.md` (the Bedrock A/B arm) in the same change.
