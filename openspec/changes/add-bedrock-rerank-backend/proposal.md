## Why

archi's retrieval rerank stage is hardwired to a single CPU cross-encoder —
FlashRank `ms-marco-MiniLM-L-12-v2`, a small, 512-token, English-only, several-year-old
model. A managed reranker (Cohere Rerank 3.5 on Amazon Bedrock) may lift retrieval
quality meaningfully, but today there is **no seam to swap the reranker backend** —
the config `reranker.model` string only ever reaches `flashrank.Ranker(...)`, so it
can pick a different *FlashRank* model but not a different *backend*, and there is no
way to A/B one reranker against another. This change adds that seam and a Bedrock
adapter behind it, gated so current behavior stays the default, and structured
**evidence-first**: a disposable, pre-registered spike — with a stronger-local-cross-encoder
arm and a data-egress approval gate — must prove the quality delta before any production seam,
dependency, or trust boundary is committed.

## What Changes

- **Evidence first — a disposable spike.** Before any production code, a *throwaway* benchmark
  reranks captured candidate pools three ways — current FlashRank `ms-marco-MiniLM-L-12-v2`, a
  stronger/newer **local** cross-encoder, and Bedrock Cohere Rerank 3.5 — under a
  **pre-registered, powered, paired** protocol with predeclared quality AND end-to-end latency
  thresholds. It answers *is a managed reranker (and its egress) even necessary*, not just *does
  Cohere win once*. No seam, no `boto3`, no config wiring in this phase.
- **Governance gate.** Documented data-owner/security approval is a **prerequisite to any live
  Bedrock call, including the spike** — the spike transmits real query text to AWS. Absent
  approval, it runs only on a sanitized synthetic/public bank.
- **Decision gate → gated production.** Only if Bedrock clears the predeclared gates *and* beats
  the stronger-local arm: a `Reranker` seam (explicit **score semantics** — ordering-only, not
  cross-backend-comparable — full-pool ranking, backend metadata), a `BedrockReranker` (raw
  boto3, bare-id→ARN normalization, `numberOfResults` = full pool), a `FallbackReranker` **+
  circuit breaker**, the `reranker.backend` config selector, and `boto3` as a dependency —
  archi's first AWS-SDK / SigV4 use.
- **Fail-open validation.** Productionization is further gated on an end-to-end workload with
  injected failures, fallback-rate telemetry, and p50/p95/p99 latency budgets — realized quality
  measured as a *function of the fallback rate*, not the fail-closed spike score.
- **No default behavior change.** FlashRank stays the default backend *and* the fallback safety
  net; existing deployments run identically (same ranking). The rendered config gains one
  documented `backend: flashrank` line, so the guarantee is **behavior-unchanged**, not
  byte-identical rendered YAML.

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
  on the on-prem FASRC box); Model Access enabled for `cohere.rerank-v3-5:0`; IAM grants
  **both `bedrock:Rerank` and `bedrock:InvokeModel`** (a direct Rerank call with
  caller-owned sources needs both — `bedrock:Rerank` alone still `AccessDenied`s), plus
  `aws-marketplace:ViewSubscriptions` + `aws-marketplace:Subscribe` for the Cohere
  third-party model; `bedrock:InvokeModel` scoped to the foundation-model ARN. Region
  `us-east-1` recommended (lowest RTT from FASRC, Cohere 3.5 supported).
- **Data egress (governance):** query text + KB child chunks leave the box to AWS.
  The KB is public FASRC docs (low risk); user queries may carry PII — needs sign-off,
  since HUIT's proxy exists precisely to keep traffic in-boundary.
- **Deploy:** the container runs a non-editable install, so this needs a **redeploy**
  to take effect. Cost is negligible ($2 / 1,000 queries; one archi retrieval = one
  query).
- **Docs:** update `docs/docs/configuration.md` (new `reranker.backend` keys) and
  `docs/docs/benchmarking.md` (the Bedrock A/B arm) in the same change.
