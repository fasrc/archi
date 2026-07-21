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
- A `Reranker` seam that decouples the rerank stage from FlashRank, with FlashRank behavior
  preserved bit-for-bit as the default.
- A `BedrockReranker` (Cohere Rerank 3.5) adapter behind the seam, config-selectable.
- Graceful fallback to local FlashRank when the remote reranker errors/times out.
- A Bedrock arm in the existing A/B harness that measures the RAGAS delta vs FlashRank.

**Non-Goals:**
- Making Bedrock the production default. Default stays FlashRank until the A/B justifies a flip
  (that flip, if it happens, is a separate change).
- Production credential plumbing / deploy / Model-Access provisioning beyond what the A/B spike
  needs. Called out here, sequenced after the delta is proven.
- Reranking parent nodes, embeddings via Bedrock, or Bedrock Knowledge Bases. Out of scope.
- Replacing FlashRank. It stays — as default *and* as the fallback safety net.

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

### D6 — Spike-first sequencing
Ship the seam + `BedrockReranker` + a harness arm **first**; measure RAGAS delta on the FASRC
bank; only then decide on productionization (creds, deploy, default flip). The seam refactor is
worth doing regardless — it's what makes the A/B possible and unblocks any future reranker.
*Why:* de-risks credential/deploy/egress work behind a proven quality win instead of ahead of it.

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

1. Land the seam + `FlashRankReranker` (pure refactor, no behavior change) — TDD, gate green.
2. Add `BedrockReranker` + `FallbackReranker` + config selector, default `flashrank`. `boto3`
   into `pyproject.toml`.
3. Add the Bedrock A/B arm — **isolating the reranker as the only variable**. The existing
   `hierarchical_rerank_ab` arms use distinct `DATA_PATH`s because they compare *chunking*, so
   each re-ingests; cloning that pattern for a reranker-only comparison lets scrape/ingest drift
   change the candidate pool independently of the reranker and confound the delta. Instead the
   FlashRank and Bedrock arms MUST share one ingested index/snapshot (same `DATA_PATH`, no
   re-scrape) so `hybrid_search` yields identical candidates and only the rerank step differs.
   Run with Bedrock **fallback disabled** (D4 exception); record the RAGAS delta in an ADR.
4. **Decision gate:** delta worth it? If no → stop; FlashRank remains, seam stays as a reusable
   asset. If yes → separate change for creds plumbing, Model Access, deploy, and any default
   flip. That change's IAM role needs **both** `bedrock:Rerank` and `bedrock:InvokeModel`
   (scoped to the foundation-model ARN) plus `aws-marketplace:ViewSubscriptions` /
   `aws-marketplace:Subscribe` for the Cohere third-party model — `bedrock:Rerank` alone
   `AccessDenied`s.

**Rollback:** set `reranker.backend: flashrank` (or omit it) and redeploy — the local path is
untouched and always available.

## Open Questions

- **Account/region:** FASRC vs HUIT AWS account; region (lean `us-east-1` for RTT + Cohere 3.5
  support). Amazon Rerank 1.0 is *not* in `us-east-1` (forces `us-west-2` if that model is ever
  wanted).
- **Data-egress sign-off:** is sending query text (possibly PII) + public KB chunks to AWS
  acceptable given HUIT's in-boundary posture? Owner decision before productionization.
- **Fallback policy depth:** is a circuit-breaker (stop paying the 2s timeout during a sustained
  outage) needed for v1, or is try/except fallback enough? (Leaning: enough for v1.)
- **A/B bank:** headline run wants the operator-local ServiceNow query bank
  (`snow_ragas_queries_pt1.json`, gitignored) staged, matching both arms.

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
