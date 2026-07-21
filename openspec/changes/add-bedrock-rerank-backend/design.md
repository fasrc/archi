## Context

archi's hierarchical retriever reranks a pool of ~20 hybrid (BM25 + vector) **child**
candidates with a CPU cross-encoder, then maps each child hit back to its **parent**
context node, deduplicates parents, and returns the top-N. The cross-encoder is FlashRank
`ms-marco-MiniLM-L-12-v2` (ONNX, in-process). The rerank stage is default-on in prod and
delivered +0.108 mean RAGAS (+19%) in the issue #32 A/B (ADR 0003).

**Current wiring (grounded):**

```
factory.build_vector_retriever (factory.py:54)
  reranker_cfg.get("model", DEFAULT_RERANKER_MODEL)          # a STRING
        │
        ▼
LlamaIndexHierarchicalRetriever._rerank (hierarchical_retriever.py:207)
  from flashrank import RerankRequest
  ranker = self.reranker or _get_cached_ranker(self.reranker_model)
  ranked = ranker.rerank(RerankRequest(query=..., passages=...))   # FlashRank-only
        │
        ▼
_get_relevant_documents (:243) walks `ranked` in order,
  maps candidates[index] → parent_id → dedup → top-N
```

The `reranker.model` config value flows all the way to `flashrank.Ranker(model_name=...)`.
It can select a different *FlashRank* model but not a different *backend*, and nothing lets
one reranker be A/B'd against another. This design adds a backend seam and a managed-reranker
(Cohere Rerank 3.5 on Amazon Bedrock) adapter behind it.

**Constraints:**
- The container runs a non-editable install (`pip install .`); a new runtime dep MUST be in
  `pyproject.toml` `dependencies`, and code changes need a **redeploy** to take effect.
- The on-prem FASRC box has **no EC2 instance role**, so boto3 cannot auto-discover creds;
  credentials are injected the way `HUIT_API_KEY` is (`~/.secrets/` → env).
- archi's existing "Bedrock" access (`huit_bedrock_provider.py`) is a Harvard **API-gateway
  proxy** that speaks Anthropic-Messages generation over `x-api-key` — it is **not** the AWS
  Bedrock SDK and does **not** expose the rerank operation (see Threads §1).

## Threads pulled (how we got here)

This design is the record of an exploration. The threads, in order:

**Thread 1 — "Bedrock" is two unrelated things.** archi's `huit_bedrock_provider.py` fronts
`go.apis.huit.harvard.edu/ais-bedrock-llm/v2/.../invoke`: an Anthropic-Messages *generation*
gateway, `x-api-key` auth, Claude-only, plain `requests`. The AWS **Rerank API** is a different
service (`bedrock-agent-runtime`), different auth (IAM/SigV4/boto3), different models
(`cohere.rerank-v3-5:0`, `amazon.rerank-v1:0`). The HUIT door is the wrong door — it does not
front rerank. This forked the work: (①) HUIT exposes rerank? — no evidence, gateway is
generation-only; (②) LLM-as-reranker via HUIT Claude — reachable but the wrong tool (a
generation model emitting relevance scores: slow, per-token, finicky listwise parsing);
(③) direct AWS Bedrock rerank via boto3 — the technically-right cross-encoder; (④) keep
FlashRank.

**Thread 2 — the blocker cleared.** AWS accounts are available (FASRC/HUIT), so ③ needs no
dependency on HUIT expanding its gateway. The fork collapses to ③ vs the ④ baseline. ② is a
trap wearing the same "Bedrock" label and is rejected.

**Thread 3 — cost is a rounding error.** Cohere Rerank 3.5 on Bedrock is **$2.00 / 1,000
queries**, where one query = up to 100 chunks, each ≤ 500 tokens. archi sends ~20 short child
chunks → **one retrieval = one query = $0.002**. Negligible at dev/nightly volume. (Symmetry
worth recording: the 500-tok/doc cap is the same *shape* as MiniLM's 512-token window — both
are satisfied *because archi reranks short children, not fat 2048-char parents*. Reranking
parents would blow past both — Cohere would split-and-bill, MiniLM would truncate.)

**Thread 4 — the A/B is not a free config swap.** The treatment arm already has a commented
`reranker.model` hook, but that string only reaches FlashRank (see Context). A real Cohere arm
needs the backend seam. Good news: the harness *structure* (two arms, held-fixed candidate
generation, RAGAS scoring) is fully reusable, and there is precedent for an external managed
model in the benchmark path — the RAGAS **judge** already runs on `huit_bedrock`. The rerank
call is archi's *first* boto3/SigV4 usage, though.

**Thread 5 — boto3 vs langchain-aws.** Decided in favor of raw boto3 (see Decision D3).

## Goals / Non-Goals

**Goals:**
- **Evidence first:** a *disposable* spike that measures whether a managed reranker (Cohere
  Rerank 3.5) beats both the current local FlashRank model *and* a stronger/newer **local**
  cross-encoder, under a pre-registered, powered, paired benchmark with predeclared quality
  **and** end-to-end latency thresholds (D6, D7).
- A `Reranker` seam + `BedrockReranker` adapter + graceful fallback — built as production code
  **only if the spike clears the predeclared gates** (D6).
- If gated in: config-selectable backend (FlashRank default), and fail-open production
  validation — fallback-rate telemetry, circuit breaker, tail-latency budgets (D9).

**Non-Goals:**
- Merging a production seam, a `boto3` dependency, or an AWS trust boundary *before* the spike
  proves Bedrock warrants them. The spike is throwaway; production is a gated follow-on (D6).
- Any live Bedrock call before documented data-egress approval (D8).
- Making Bedrock the default. FlashRank stays the default *and* the fallback safety net unless
  the gates plus a default-flip decision say otherwise.
- Reranking parent nodes, embeddings via Bedrock, or Bedrock Knowledge Bases. Out of scope.

## Decisions

### D1 — A minimal `Reranker` protocol, not a framework
Introduce `rerank(query: str, passages: list[str]) -> list[tuple[int, float]]`, returning
`(original_index, score)` **sorted descending over the full passage list**. `_rerank` becomes
backend-agnostic; `FlashRankReranker` wraps today's `_get_cached_ranker` + `RerankRequest`.
*Why this shape:* the downstream code (`_get_relevant_documents`) needs the candidate **index**
to map child → parent and dedup — so the contract is index-preserving by design.
*Alternative rejected:* returning reranked `Document`s (loses the index; see D3).

### D2 — Full-candidate-pool ranking is a hard invariant
The reranker MUST return a ranking over **all** candidates, not a pre-truncated top-N. archi
does parent-dedup *after* reranking, and a child ranked #14 can be the first (best) hit for an
otherwise-unseen parent. For Bedrock this means `numberOfResults = len(passages)`, explicitly.
*Why it matters:* this is the classic bug that passes every unit test (small fixtures) and
silently starves recall in prod. Encoded as a spec requirement, not just a code comment.

### D3 — Raw boto3, not langchain-aws
Use `boto3` `bedrock-agent-runtime.rerank` directly.
*Comparison:*
- **Seam fit (decider):** boto3 returns `results: [{index, relevanceScore}]` — `index` maps
  1:1 onto D1's contract. `langchain_aws.BedrockRerank` is a `BaseDocumentCompressor`; its
  `compress_documents(documents, query)` returns reordered `Document`s with
  `metadata['relevance_score']` and **discards the input index**. To recover which candidate a
  result is, you smuggle the index through `Document.metadata` and match it back — fighting an
  abstraction that hides the one thing archi needs.
- **Dependency:** `langchain-aws` pulls `boto3` anyway **plus** a `langchain-core` version pin
  — it can drag/upgrade archi's core LangChain across the whole app. Strict superset for no
  benefit.
- **The top-N trap, worse:** `BedrockRerank.top_n` defaults to a small number; forget to
  override it and D2 is silently violated. boto3 keeps `numberOfResults` explicit and visible.
- **Precedent:** every archi provider (`anthropic_`, `gemini_`, `openai_`, `huit_bedrock_`) is
  a thin adapter on `langchain_core` + raw transport (`requests`/SDK); none pulls a
  `langchain-*` integration package. Raw boto3 matches the house style; langchain-aws breaks it.
- **The one world langchain-aws wins:** if archi were built on
  `ContextualCompressionRetriever` (base retriever → compressor), `BedrockRerank` would slot in
  with near-zero glue. archi isn't — see Future Exploration.
*Decision:* raw boto3. Rejected langchain-aws.

### D4 — Fallback wrapper keeps the network off the hard path
`FallbackReranker(primary=BedrockReranker, secondary=FlashRankReranker)` catches any
error/timeout from the remote primary and degrades to the local reranker, so retrieval never
hard-depends on an external call. boto3 client configured with tight timeouts
(`connect_timeout≈1s`, `read_timeout≈2s`, `max_attempts≈2`). FlashRank is **pre-warmed at
startup** even when Bedrock is primary, so the first fallback doesn't pay the one-time ONNX
load exactly when Bedrock is already failing.
*Alternative rejected:* hard-fail on remote error — unacceptable to make every answer depend on
a third-party network call when a local reranker exists.
*Benchmark exception (see Migration step 3):* the A/B arm runs the Bedrock backend with
fallback **disabled** (fail closed). Silent fallback would let the run measure FlashRank while
RAGAS records it as the Bedrock treatment — corrupting the exact experiment the spike exists to
run. Fail closed, or assert zero fallback events before accepting the delta.

### D5 — Config-gated, FlashRank default, backend-aware validation
`data_manager.retrievers.hierarchical_rerank.reranker.backend` selects `flashrank` (default) or
`bedrock`; Bedrock reads `model` (ARN or bare id), `region`, `timeout_s`, `fallback`.
`factory.build_vector_retriever` builds the selected backend and, for Bedrock, wraps it in
`FallbackReranker`.

Two footguns the config seam must close (both were review findings):
- **Model is backend-scoped.** Today the template renders `reranker.model` with the FlashRank
  default (`ms-marco-MiniLM-L-12-v2`) whenever the operator omits it. Under `backend: bedrock`
  that hands a FlashRank model name to the Bedrock adapter → an invalid ARN. So the factory
  MUST apply a **backend-specific default** (Bedrock → `cohere.rerank-v3-5:0`) and **fail fast**
  if a FlashRank-shaped model is configured under `bedrock` (never silently fall through to
  FlashRank — see D4 benchmark exception).
- **Model id → ARN normalization.** The Rerank API requires
  `modelConfiguration.modelArn = arn:aws:bedrock:<region>::foundation-model/<id>`. The adapter
  normalizes a bare `cohere.rerank-v3-5:0` to that ARN using the configured `region`, so a
  config carrying either the bare id or a full ARN works; a bare id with no region fails fast.

*Guarantee scope (F6):* omitting `backend` keeps retrieval **behaving** exactly as today
(default backend flashrank, identical ranking). Because the template always renders its keys
with Jinja defaults (as `enabled`/`candidate_pool_size` already do), the rendered YAML gains one
documented `backend: flashrank` line — so the promise is *behavior-unchanged*, not
byte-identical rendered output. The TDD asserts the default backend and identical ranking, not
byte equality of the rendered file.

### D6 — Evidence before production abstraction (disposable spike first)
*Adversarial-review finding (medium).* The earlier sequencing landed the seam, the
`BedrockReranker`, the `boto3` dependency, and the harness arm *before* any evidence, and
justified the seam as "worth it regardless." That commits a permanent interface + a new AWS
dependency + a trust boundary on one hypothetical consumer — and the base rerank feature already
captured +19% RAGAS, so the reranker-*only* residual may be small. Re-sequenced:
1. **Disposable spike** — a throwaway script (NOT wired into the retriever, no `boto3` in
   `pyproject.toml`, no config) that captures the existing FlashRank candidate pools for the
   eval bank and reranks them three ways: current FlashRank `ms-marco-MiniLM-L-12-v2`, a
   stronger/newer **local** cross-encoder, and Bedrock Cohere. Scored under D7.
2. **Decision gate** — Bedrock must clear the predeclared quality **and** latency gates *and*
   beat the stronger-local arm by enough to justify egress. Else adopt the stronger local model
   (no egress) or keep FlashRank; **discard the spike**.
3. **Production (gated only)** — build the seam, `BedrockReranker`, `boto3` dep, fallback +
   circuit breaker, and config *then*, deriving the interface from the demonstrated consumers
   and specifying **score semantics** (ordering-only within one backend, not cross-backend
   comparable), ranking completeness, and backend metadata — not a hard-coded pointwise
   `(index, score)` with unstated semantics.
*Why:* de-risks a permanent dependency/abstraction + a compliance boundary behind proven,
powered evidence rather than ahead of it.

### D7 — Pre-registered, powered, paired evaluation (with a stronger-local arm)
*Adversarial-review finding (high).* "Capture a RAGAS delta and ask if it's worth it" cannot
separate a real reranker gain from LLM-judge + sampling noise — especially a small reranker-only
residual on a bank of unknown size. Before the spike runs:
- **Version a privacy-safe eval bank** with committed cardinality and representativeness
  criteria (D8 constrains *which* bank).
- **Power/variance analysis** → a minimum detectable effect; **pre-register** paired per-query
  metrics, confidence intervals, and a repeated-judge strategy.
- **Predeclare thresholds:** a minimum quality improvement AND an end-to-end latency budget —
  both must be met to pass.
- **Three arms**, so the experiment answers *is managed egress necessary* (Bedrock vs
  stronger-local vs current), not merely *does Cohere beat the current small model once*.

### D8 — Data-egress approval gates the spike, not just productionization
*Adversarial-review finding (high).* The spike itself transmits the (gitignored, real
ServiceNow-ticket) query bank to AWS — it crosses the exact trust boundary the plan had deferred
to "productionization," and a benchmark cannot legitimize an experiment that was unauthorized to
disclose its inputs. So documented **data-owner / security approval is a prerequisite to any
live Bedrock call, including the spike**, defining allowed datasets, AWS account/region, and
retention/logging terms. Absent approval, the spike runs **only on a sanitized synthetic or
public bank**.

### D9 — Fail-open production must be validated as it will run
*Adversarial-review finding (high).* The spike runs fail-*closed* (D4 exception) to measure
Bedrock cleanly, but production runs fail-*open*: every remote timeout/throttle silently
substitutes FlashRank after paying the remote delay, and an agent retrieves multiple times per
turn. A clean-run score therefore says nothing about steady-state realized quality or tail
latency. Productionization (the gated follow-on) MUST validate on an end-to-end agent workload
with injected timeout/throttle/outage cases, **fallback-rate telemetry**, a **circuit breaker**,
backend/fallback status exposed in traces, and p50/p95/p99 latency budgets — and report expected
realized quality as a *function of the observed fallback rate*, not the fail-closed score.

## Risks / Trade-offs

- **Network dependency on the answer path** → D4 fallback to in-process FlashRank; tight
  timeouts; pre-warm the fallback.
- **`numberOfResults` starves parent-dedup** → D2 invariant + a spec scenario + a test that
  asserts a low-ranked-but-unique-parent candidate survives.
- **New boto3/SigV4 surface (archi's first)** → isolated in one adapter module; injectable
  client keeps unit tests hermetic (no network, no AWS import in the FlashRank path).
- **`boto3` missing from `pyproject.toml` `dependencies`** → container crash-loops on
  `ModuleNotFoundError`, and **neither the gate nor CI catches it** (only a live deploy does).
  Explicit task + deploy-verify smoke.
- **Data egress of possibly-PII queries to AWS** → governance sign-off (Open Questions); the KB
  content itself is public FASRC docs.
- **Cost surprise from oversized pools** → one query covers ≤100 chunks; archi's ~20 stays a
  single query. A pool >100 would silently bill as multiple; guard/log if pool size grows.

## Migration Plan

1. **Gate — data egress (D8).** Obtain documented data-owner/security approval (allowed
   datasets, AWS account/region, retention/logging), OR designate a sanitized synthetic/public
   bank. **No live Bedrock call before this.**
2. **Pre-register (D7).** Version the eval bank (committed cardinality + representativeness);
   power/variance → MDE; predeclare paired metrics, CIs, and quality + latency thresholds;
   define the three arms (FlashRank / stronger-local / Bedrock). Commit the pre-registration.
3. **Disposable spike (D6).** Capture the shared candidate pools once; rerank three ways
   fail-closed; score. ADR records each arm against the predeclared gates.
4. **Decision gate.** Bedrock clears quality+latency gates AND beats stronger-local enough to
   justify egress? No → adopt stronger-local or keep FlashRank, **discard the spike**. Yes →
   proceed to production.
5. **Production (gated).** Seam + `BedrockReranker` + `boto3` dep + fallback + circuit breaker +
   config, interface derived from real consumers with explicit score semantics; least-privilege
   IAM role — **both** `bedrock:Rerank` and `bedrock:InvokeModel` (scoped to the foundation-model
   ARN) plus `aws-marketplace:ViewSubscriptions` / `aws-marketplace:Subscribe` for the Cohere
   third-party model (`bedrock:Rerank` alone `AccessDenied`s); creds plumbing; deploy.
6. **Fail-open validation (D9).** End-to-end agent workload, injected failures, fallback-rate
   telemetry, p50/p95/p99 budgets, realized-quality-as-f(fallback-rate); then any default flip.

**Rollback:** set `reranker.backend: flashrank` (or omit it) and redeploy — the local path is
untouched and always available.

## Open Questions

- **Account/region:** FASRC vs HUIT AWS account; region (lean `us-east-1` for RTT + Cohere 3.5
  support). Amazon Rerank 1.0 is *not* in `us-east-1` (forces `us-west-2` if that model is ever
  wanted). Part of the D8 approval.
- **Which stronger-local cross-encoder** is the third arm (e.g. `bge-reranker-base`/`-large`,
  a newer MiniLM, a mxbai/jina reranker)? Pick on the quality/latency/size curve; it may make
  Bedrock moot (no egress) — which is the point of including it (D7).
- **Does the spike even need Bedrock?** If the stronger-local arm clears the quality gate, the
  managed backend + egress + dependency may be unnecessary. The spike is designed to answer this.

*Resolved by this revision (were open questions):* data-egress sign-off is now a hard
prerequisite (D8), not a deferred question; the circuit breaker is required for production
validation (D9), not "maybe v1"; the eval bank must be versioned and privacy-safe (D7/D8), not
the gitignored ServiceNow bank used ad hoc.

## Future Exploration — ContextualCompressionRetriever + langchain-aws

Deliberately deferred, recorded so it isn't re-derived from scratch.

**The idea:** LangChain's native reranking pattern is
`ContextualCompressionRetriever(base_compressor=BedrockRerank(...), base_retriever=...)` — a
base retriever produces candidates, a `BaseDocumentCompressor` reranks/compresses them. If
archi's retrieval were expressed in that shape, `langchain_aws.BedrockRerank` would drop in with
near-zero glue (no index-smuggling), and swapping rerankers would be a compressor swap.

**Why it's not this change:** archi's `LlamaIndexHierarchicalRetriever` reranks **mid-pipeline**
— on *child* candidates, *before* the child→parent mapping and dedup — and needs the candidate
index to do that mapping. `ContextualCompressionRetriever` reranks *final* documents at the
outer retriever boundary, which is a different integration point and would require restructuring
the parent-expansion logic to live either inside a custom compressor or after it. That's a
retriever-architecture change, not a reranker swap.

**What a future exploration would weigh:**
- Refactor `LlamaIndexHierarchicalRetriever` so parent-expansion is a `base_retriever` and
  reranking is a `base_compressor` — does the child→parent-before-rerank ordering even survive
  that split? (Reranking children then expanding to parents is the current, benchmarked design;
  a compressor reranks *after* whatever the base retriever returns.)
- If parents must be reranked instead of children, revisit the 500-tok/512-tok limits (Thread 3)
  — parent-length passages change the cost and truncation story for *both* Cohere and MiniLM.
- Whether adopting the `langchain-*` integration-package pattern (and its `langchain-core`
  version coupling) is worth the framework alignment, given archi has so far deliberately
  avoided those packages (Decision D3, precedent).
- Payoff: uniform reranker interface across any provider LangChain supports (Cohere, Voyage,
  Jina, cross-encoder) via one abstraction, at the cost of a retriever rearchitecture.

**Trigger to revisit:** if archi ever moves retrieval onto native LangChain retriever
composition for other reasons, fold the reranker into a compressor at that time rather than
maintaining the bespoke seam. Until then, the D1 seam is the lighter, better-fitting choice.
