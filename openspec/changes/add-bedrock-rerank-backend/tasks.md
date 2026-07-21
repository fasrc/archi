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

## 3. Fallback + config wiring

- [ ] 3.1 TDD: `FallbackReranker(primary, secondary)` — on any primary error/timeout, degrade to secondary; log the fallback (spec: "Graceful fallback when a remote reranker fails").
- [ ] 3.2 TDD the pre-warm path: local fallback reranker is initialized ahead of first use when `bedrock` is primary (no cold-start at failure time).
- [ ] 3.3 Extend `factory.build_vector_retriever` to read `reranker.backend` (`flashrank` default | `bedrock`) + Bedrock sub-keys (`model`, `region`, `timeout_s`, `fallback`); build the selected backend, wrapping Bedrock in `FallbackReranker`. TDD: `flashrank`/absent → `FlashRankReranker`; `bedrock` → fallback-wrapped `BedrockReranker`.
- [ ] 3.4 Add the `reranker.backend` keys to `src/cli/templates/base-config.yaml` with comments; TDD that omitting them renders unchanged (default-render guarantee).

## 4. A/B harness arm (the spike)

- [ ] 4.1 Add a Bedrock arm config under `examples/benchmarking/hierarchical_rerank_ab/` (clone the treatment arm; set `reranker.backend: bedrock` + model/region); document required AWS env + Model Access in its README.
- [ ] 4.2 Run baseline (FlashRank) vs Bedrock on the FASRC bank; capture the RAGAS delta and latency.
- [ ] 4.3 Record the result in an ADR (`docs/decisions/`), including the decision gate: does the delta justify productionization?

## 5. Docs + verification

- [ ] 5.1 Update `docs/docs/configuration.md` (`reranker.backend` and Bedrock sub-keys) and `docs/docs/benchmarking.md` (the Bedrock A/B arm).
- [ ] 5.2 Full gate green (`bash scripts/gate.sh`, ≥80% diff coverage on changed lines); `openspec validate add-bedrock-rerank-backend --strict` passes.
- [ ] 5.3 Adversarial check before PR: verify the full-pool invariant and the fallback path against the code; run the suite.

## 6. Out of scope for this change (sequenced after the decision gate)

- [ ] 6.1 (Deferred — separate change) Production credential injection (`~/.secrets/` → env, mirror `HUIT_API_KEY`), Model Access provisioning, deploy, and any default-backend flip — only if task 4 shows the delta is worth it. Requires the data-egress sign-off (Open Questions in design.md).
