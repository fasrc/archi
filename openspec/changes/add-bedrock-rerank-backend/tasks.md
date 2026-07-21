## 1. Reranker seam (pure refactor, no behavior change)

- [ ] 1.1 Add a `Reranker` protocol/base — `rerank(query, passages) -> list[(index, score)]`, sorted descending over the full pool (new adapters module under `retrievers/`).
- [ ] 1.2 TDD: extract today's inline FlashRank call into `FlashRankReranker` (wraps `_get_cached_ranker` + `RerankRequest`); assert its output is identical to the pre-refactor `_rerank` for a fixed candidate pool.
- [ ] 1.3 Rewrite `LlamaIndexHierarchicalRetriever._rerank` to delegate to an injected `self._reranker` (default `FlashRankReranker(reranker_model)`); keep the existing `reranker` injection point working for tests.
- [ ] 1.4 Run the existing hierarchical-retriever tests — behavior unchanged, gate green.

## 2. Bedrock backend adapter (raw boto3)

- [ ] 2.1 Add `boto3` to `pyproject.toml` `dependencies` (match any pin in `requirements/`); note the redeploy requirement.
- [ ] 2.2 TDD: `BedrockReranker` calling `bedrock-agent-runtime.rerank` with `cohere.rerank-v3-5:0`, mapping `results[].index`/`.relevanceScore` onto the seam contract; **inject a fake client** so tests are hermetic (no network, no AWS import in the FlashRank path).
- [ ] 2.3 TDD the full-pool invariant (spec: "Reranker ranks the full candidate pool"): assert `numberOfResults == len(passages)` in the request, and that a low-ranked-but-unique-parent candidate survives parent dedup.
- [ ] 2.4 Configure the boto3 client with tight timeouts (`connect≈1s`, `read≈2s`, `max_attempts≈2`) via `botocore.config.Config`.
- [ ] 2.5 TDD model-id → ARN normalization: a bare `cohere.rerank-v3-5:0` is expanded to `arn:aws:bedrock:<region>::foundation-model/<id>` (the `modelArn` the Rerank API requires); a full ARN passes through unchanged; a bare id with no configured region fails fast with a clear error.

## 3. Fallback + config wiring

- [ ] 3.1 TDD: `FallbackReranker(primary, secondary)` — on any primary error/timeout, degrade to secondary; log the fallback (spec: "Graceful fallback when a remote reranker fails").
- [ ] 3.2 TDD the pre-warm path: local fallback reranker is initialized ahead of first use when `bedrock` is primary (no cold-start at failure time).
- [ ] 3.3 Extend `factory.build_vector_retriever` to read `reranker.backend` (`flashrank` default | `bedrock`) + Bedrock sub-keys (`model`, `region`, `timeout_s`, `fallback`); build the selected backend, wrapping Bedrock in `FallbackReranker`. TDD: `flashrank`/absent → `FlashRankReranker`; `bedrock` → fallback-wrapped `BedrockReranker`.
- [ ] 3.3a Backend-aware model resolution/validation: under `backend: bedrock`, default the model to `cohere.rerank-v3-5:0` when omitted (NOT the FlashRank default the template renders), and **fail fast** if a FlashRank-shaped model id is configured under `bedrock` — never silently fall through to FlashRank. TDD both.
- [ ] 3.4 Add the `reranker.backend` keys to `src/cli/templates/base-config.yaml` with comments (rendered with Jinja defaults, matching the existing `enabled`/`candidate_pool_size` pattern). TDD the **behavior** guarantee: omitting `backend` selects `flashrank` and produces the identical ranking — NOT byte-identical rendered YAML (the render gains a documented `backend: flashrank` line by design; assert the default value + behavior, not file bytes).

## 4. A/B harness arm (the spike)

- [ ] 4.1 Add a Bedrock arm under `examples/benchmarking/hierarchical_rerank_ab/` that **isolates the reranker as the only variable**: the FlashRank and Bedrock arms MUST share one ingested index/snapshot (same `DATA_PATH`, no re-scrape/re-ingest) so `hybrid_search` returns identical candidate pools and only the rerank step differs — do NOT clone the chunking-comparison pattern that re-ingests per arm (that lets corpus drift confound the delta). Set `reranker.backend: bedrock` + model/region, and `fallback: none` (**fail closed**). Document required AWS env + Model Access in its README.
- [ ] 4.2 Run FlashRank vs Bedrock over the shared index on the FASRC bank; capture the RAGAS delta and latency. **Assert zero fallback events** for the Bedrock arm (a fallback means the arm measured FlashRank, not Bedrock — discard and fix rather than record the delta).
- [ ] 4.3 Record the result in an ADR (`docs/decisions/`), including the decision gate: does the delta justify productionization?

## 5. Docs + verification

- [ ] 5.1 Update `docs/docs/configuration.md` (`reranker.backend` and Bedrock sub-keys) and `docs/docs/benchmarking.md` (the Bedrock A/B arm).
- [ ] 5.2 Full gate green (`bash scripts/gate.sh`, ≥80% diff coverage on changed lines); `openspec validate add-bedrock-rerank-backend --strict` passes.
- [ ] 5.3 Adversarial check before PR: verify the full-pool invariant and the fallback path against the code; run the suite.

## 6. Out of scope for this change (sequenced after the decision gate)

- [ ] 6.1 (Deferred — separate change) Production credential injection (`~/.secrets/` → env, mirror `HUIT_API_KEY`), Model Access provisioning, the least-privilege IAM role (**both `bedrock:Rerank` and `bedrock:InvokeModel`** scoped to the foundation-model ARN, plus `aws-marketplace:ViewSubscriptions` / `aws-marketplace:Subscribe` for the Cohere third-party model), deploy, and any default-backend flip — only if task 4 shows the delta is worth it. Requires the data-egress sign-off (Open Questions in design.md).
