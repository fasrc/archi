> **Sequencing (D6):** evidence gates production. Phases 1–2 are a *disposable* spike — no
> production seam, no `boto3` in `pyproject.toml`, no config wiring. Phases 3–4 are built
> **only if** the phase-2 ADR selects Bedrock (decision gate). Phase 1 blocks all live-Bedrock
> work.

## 1. Governance + pre-registration gate (blocks all live-Bedrock work — D8, D7)

- [ ] 1.1 **(D8, blocker)** Obtain documented data-owner/security approval for sending query text to AWS Bedrock: allowed datasets, AWS account + region, retention/logging terms. If approval is not granted, designate a **sanitized synthetic or public** evaluation bank and use ONLY that. No live Bedrock call before this task is done.
- [ ] 1.2 Version a privacy-safe evaluation bank with committed cardinality and representativeness criteria (record which bank, its size, and how it was sanitized/selected).
- [ ] 1.3 Pre-register the benchmark to `docs/decisions/` **before running anything**: power/variance analysis → minimum detectable effect; paired per-query metrics with confidence intervals; repeated-judge strategy; predeclared minimum quality improvement AND end-to-end latency budget (both must pass). Name the three arms: current FlashRank / stronger-local cross-encoder / Bedrock Cohere.

## 2. Disposable spike — evidence before any production code (D6)

- [ ] 2.1 Write a **throwaway** spike script (NOT wired into the retriever, no `boto3` in `pyproject.toml`) that captures the existing FlashRank candidate pools once for the eval bank — identical pools reused across all three arms so only the rerank step varies (kills corpus/index drift).
- [ ] 2.2 Rerank each captured pool three ways: (a) current FlashRank `ms-marco-MiniLM-L-12-v2`; (b) a stronger/newer **local** cross-encoder; (c) Bedrock Cohere Rerank 3.5. Run **fail-closed** — a Bedrock error aborts that arm, never silently substitutes FlashRank. For Bedrock, normalize the model id to `arn:aws:bedrock:<region>::foundation-model/<id>` and request `numberOfResults = len(pool)`.
- [ ] 2.3 Score all three arms under the pre-registered protocol (paired metrics + CIs); capture quality AND end-to-end latency for each.
- [ ] 2.4 ADR (`docs/decisions/`): report each arm against the predeclared gates. Decision: **(a)** Bedrock clears quality+latency gates AND beats the stronger-local arm by enough to justify egress → productionize (phase 3); **(b)** stronger-local wins/ties → adopt it, no egress; **(c)** neither meaningfully beats FlashRank → keep FlashRank. Discard the spike script in every branch.

## 3. Production reranker backend — GATED on the phase-2 decision (a) only (D1/D2/D5/D6)

- [ ] 3.1 `Reranker` seam derived from the demonstrated consumers, with EXPLICIT contract: **score semantics** (ordering-only within one backend, NOT cross-backend-comparable — the retriever relies on order, not absolute scores), **ranking completeness** (full pool — D2), and backend metadata. TDD.
- [ ] 3.2 Extract today's inline FlashRank call into `FlashRankReranker` behind the seam; assert identical ranking to the pre-refactor `_rerank` for a fixed pool.
- [ ] 3.3 `BedrockReranker` (raw boto3, **injectable fake client** for hermetic tests; `numberOfResults = len(pool)`; model-id→ARN normalization; tight `botocore.config.Config` timeouts). Add `boto3` to `pyproject.toml` `dependencies` (redeploy required — the deploy image `pip install .`s; neither gate nor CI catches a missing dep).
- [ ] 3.4 `FallbackReranker` (remote primary → local FlashRank on error/timeout; pre-warm FlashRank at startup) **plus a circuit breaker** (D9 — stop hammering a failing remote for a cooldown). TDD both.
- [ ] 3.5 `factory.build_vector_retriever` reads `reranker.backend` (`flashrank` default | `bedrock`) + Bedrock sub-keys (`model`, `region`, `timeout_s`, `fallback`); apply a **backend-specific model default** (Bedrock → `cohere.rerank-v3-5:0`) and **fail fast** on a FlashRank-shaped model under `bedrock` (never silent fallback). TDD.
- [ ] 3.6 Add `reranker.backend` keys to `src/cli/templates/base-config.yaml` with comments. TDD the **behavior** guarantee: omitting `backend` selects `flashrank` and produces the identical ranking — NOT byte-identical rendered YAML (the render gains a documented `backend: flashrank` line by design).

## 4. Fail-open production validation — GATED (D9)

- [ ] 4.1 Exercise an end-to-end agent workload with realistic repeated retrievals; inject timeout/throttle/outage cases; record **fallback-rate telemetry** and expose backend/fallback status in traces.
- [ ] 4.2 Measure p50/p95/p99 end-to-end latency against the predeclared budget; compute expected realized quality as a **function of the observed fallback rate** (NOT the fail-closed spike score).
- [ ] 4.3 Only if realized quality + tail latency clear the gates: credential injection (`~/.secrets/` → env, mirror `HUIT_API_KEY`), Model Access, least-privilege IAM (**both `bedrock:Rerank` and `bedrock:InvokeModel`** scoped to the foundation-model ARN, plus `aws-marketplace:ViewSubscriptions` / `aws-marketplace:Subscribe` for the Cohere third-party model), deploy, and any default-backend flip.

## 5. Docs + verification (per phase, as each lands)

- [ ] 5.1 Update `docs/docs/benchmarking.md` (the three-arm pre-registered spike) when phase 2 lands; `docs/docs/configuration.md` (`reranker.backend` keys) when phase 3 lands.
- [ ] 5.2 Gate green (`bash scripts/gate.sh`, ≥80% diff coverage on changed lines) for each PR; `openspec validate add-bedrock-rerank-backend --strict` passes.
