## Context

The hierarchical-rerank retriever (PR #31) is implemented, default-off, and correctness-
verified on dev. The remaining task 6.2 (issue #32) is pure evaluation: produce
quality/latency/image-size evidence comparing it to the baseline. The `archi evaluate`
harness exists and is mature for *prompt* sweeps — it decouples the RAGAS judge from the
system-under-test and records per-query latency (`time_elapsed`). But its `-cd`
config-directory mode sweeps only `services.benchmarking` (prompt/agent/model) over a
**single, once-ingested corpus**; it cannot vary chunking or the retriever per arm (see
D1). A retrieval A/B therefore runs as **two separate deploy+ingest+evaluate passes**
(baseline, then treatment) compared offline. So the work is *feeding* the harness twice,
plus one small enabler in the ingestion code (configurable chunk sizes).

This session already drafted the data/config artifacts: two question banks and a
held-fixed baseline/treatment config pair under `examples/benchmarking/`. The change
formalizes those, adds the one code enabler, runs the benchmark on the deployment, and
records a recommendation.

Constraint: the run itself is `needs-deploy` — it requires the FASRC vLLM SUT, HuggingFace
embeddings, a Bedrock judge (`HUIT_API_KEY`), and a double corpus ingest. The local gate
cannot execute it. Only the chunk-size plumbing (and its unit tests) is gate-verifiable.

## Goals / Non-Goals

**Goals:**
- A reproducible, apples-to-apples A/B (baseline vs treatment) over the FASRC corpus.
- Recorded deltas: RAGAS quality (overall + by question type), warm/cold latency,
  built-image size.
- Make parent/child chunk sizes configurable so the size recommendation is data-driven.
- A durable recommendation (default-on/off, parent/child sizes, `bm25_weight`).

**Non-Goals:**
- Changing the production default (the feature stays default-off until the recommendation).
- Building new benchmarking infrastructure (the harness exists; we configure it).
- Tuning the reranker model choice or candidate-pool internals beyond what the config
  knobs already expose.
- SOURCES-mode (URL-match) scoring — the primary banks are RAGAS-only.

## Decisions

**D1 — Two separate deploy+ingest+evaluate passes, compared offline (NOT one `-cd` run).**
The `archi evaluate -cd` directory mechanism is a *prompt sweep over a single fixed
corpus*, not a retrieval A/B. Verified against the code: `ConfigurationManager._append`
requires the whole `global` block to be identical across configs (only
`services.benchmarking` is exempt; unit test `test_differing_global_still_rejected`
asserts a differing `DATA_PATH` is rejected), and the corpus is ingested **once** at
deploy — `run()` waits for ingestion a single time then loops configs over the same
vectorstore, reading retriever/chunking from the once-seeded Postgres static config
(`archi()` → `get_full_config()`, cached). So two arms differing in chunking + retriever
cannot be expressed in one `-cd` run; both would silently share one ingest + one retriever
config. Therefore each arm runs as its **own** deployment: deploy + ingest with the
baseline config, `archi evaluate -n <name> -c baseline_*.yaml`; then redeploy + re-ingest
with the treatment config and evaluate again; compare the two RAGAS aggregates offline.
Alternative (extend the harness to re-ingest + reseed config per arm within one `-cd` run)
was rejected for this change: a substantial, deploy-bound harness change larger than this
benchmark's scope — left as a possible future enhancement.

**D2 — Hold candidate-generation weights equal across arms.**
Both `HybridRetriever` and `LlamaIndexHierarchicalRetriever` read `bm25_weight` /
`semantic_weight` from `retrievers.hybrid_retriever` (`factory.py`). If they differed, a
measured delta would conflate the rerank effect with a candidate-pool effect. The config
pair sets them identically; `bm25_weight` tuning is a *separate* sweep, not part of the
core A/B.

**D3 — Distinct `DATA_PATH` per arm (each arm is its own deployment/ingest).**
Because each arm is run as a separate deploy+ingest pass (D1), each naturally builds its
own vectorstore at its own `DATA_PATH` (baseline character-split vs treatment hierarchical).
The two config files are therefore run **one at a time** with `-c`, never together in a
`-cd` directory (the loader would reject the differing `global` block, and a shared corpus
ingest would invalidate the comparison anyway).

**D4 — Decouple judge from SUT.**
SUT = FASRC vLLM Qwen (via `provider: local` + `ollama_url` as base_url); judge =
HUIT Bedrock Claude Sonnet 4.5, *pinned* (not the rolling alias) for reproducibility. A
model grading its own output inflates scores; pinning keeps rounds comparable.

**D5 — Two complementary banks.**
`snow_ragas_queries_pt1.json` (27 real ServiceNow tickets) gives the realistic headline
quality delta; `fasrc_ragas_queries.json` (21 typed) lets us slice by `anchor_type` to
see *which* question type the treatment moves — the hypothesis is that returning parent
context helps `reasoning`/synthesis more than `easy_retrieve` lookups.

**D6 — Plumb chunk sizes through config (the scoping fork, resolved).**
`parent_chunk_size`/`child_chunk_size` already exist as parameters in
`build_hierarchical_nodes` but the call site (`manager.py:795`) passes only `strategy`.
We read them from `data_manager.chunking` and pass them through, defaulting to the current
constants when unset. Chosen over "benchmark defaults only" because the issue's acceptance
explicitly asks for a parent/child-size recommendation — and you cannot sweep what you
cannot configure. The change is small, gate-verifiable, and not deploy-bound.

**D7 — Warm-vs-cold latency via first-query isolation.**
The treatment's first query pays a one-time FlashRank ONNX load (~45s on dev vs ~8s
baseline). Rather than instrument the harness, the protocol discards the first treatment
query (or prepends a throwaway warmup question) and averages the rest. Cheaper than code
changes and adequate for a one-time measurement.

## Risks / Trade-offs

- **Stale ground-truth answers** → Banks were grounded against live KB text; answers carry
  `DRAFT`/operator-confirm notes (live docs already drifted, e.g. `--gres=gpu:N` →
  `--gpus=1`). Mitigation: operator confirms before the scored run is treated as
  authoritative.
- **SOURCES-mode URL drift** → The typed bank's URLs may not match ingested sitemap slugs.
  Mitigation: run RAGAS-mode (primary); reconcile URLs before any SOURCES scoring.
- **Judge bias / availability** → Bedrock judge needs `HUIT_API_KEY` and network.
  Mitigation: pinned model; document the env requirement; degrade to same-model judge only
  if Bedrock is unavailable (and flag the result as biased).
- **Small bank size** → 27 + 21 questions is modest; per-type slices are smaller still.
  Mitigation: report counts alongside metrics; treat large effects as signal, small ones
  as directional. Banks are extensible (`_pt1` naming anticipates more).
- **Double-ingest cost on dev** → Two full ingests of the corpus. Mitigation: run on the
  dev box during a benchmarking window; reuse arm vectorstores across re-runs via stable
  `DATA_PATH`.

## Migration Plan

No production migration. The chunk-size config keys are additive and backward-compatible
(absent → existing defaults). Rollback = drop the keys. The benchmark run is an offline
measurement; it does not alter the live deployment. The recommendation may *later* trigger
a separate default-on change — out of scope here.

## Open Questions

- Final parent/child size grid to sweep (e.g. 1024/256, 2048/512, 4096/512) — settle once
  the plumbing lands and a first default-size run shows the quality/latency baseline.
- Whether to cap the agent's `search_vectorstore_hybrid` tool calls during the run (it
  changes the candidate pool the reranker sees) — record the cap setting alongside results.
- Whether the headline number reports the snow bank, the typed bank, or both combined.
