# 0003 — Enable hierarchical-rerank retrieval by default

**Status:** Accepted (branch-scoped: `chore/benchmark-task-1.3-out-of-scope`)
**Task:** `6.3 Write a decision record under docs/decisions/ with the recommendation (default-on/off, parent/child sizes, bm25_weight), each setting citing its measured number`
**Change:** `openspec/changes/benchmark-hierarchical-rerank` (issue #32)

## Context

Issue #31 added hierarchical-rerank retrieval (structural parent/child chunking at
ingest + a FlashRank cross-encoder rerank at retrieval, returning parent context),
shipped **default-off**. Issue #32 asks whether it should be the default, and what
parent/child chunk sizes and `bm25_weight` to recommend — answered with measured
numbers rather than intuition.

### Method

A two-run A/B (design D1: the `archi evaluate -cd` harness sweeps prompts over one
fixed corpus and cannot vary chunking/retriever per arm, so each arm is its own
deploy → ingest → evaluate pass, compared offline):

- **Corpus:** the live FASRC list (`config/lists/sources.list`, 347 URLs → **330
  docs** ingested; baseline 4,776 chunks, treatment 4,982 nodes).
- **Bank:** `examples/benchmarking/fasrc_ragas_queries.json` (21 typed questions:
  10 `easy_retrieve`, 8 `reasoning`, 3 `should_refuse`).
- **SUT:** FASRC vLLM `Qwen3.6-35B` (`provider: local`, openai_compat — requires the
  fix in #74). **Judge:** HUIT Bedrock `claude-sonnet-4-5`. **Metric embeddings:**
  local HuggingFace. Both arms held identical except `data_manager.chunking` +
  `data_manager.retrievers` (baseline = character splitter + HybridRetriever, k=5;
  treatment = sentence hierarchical chunking + FlashRank `ms-marco-MiniLM-L-12-v2`,
  `candidate_pool_size=20`, `top_n=5`), `bm25_weight=0.6` in both.

### Measured results

RAGAS aggregate over 21 questions:

| metric | baseline | treatment | delta |
|---|---:|---:|---:|
| answer_relevancy | 0.692 | 0.667 | −0.025 |
| faithfulness | 0.489 | 0.580 | +0.091 |
| context_precision | 0.491 | 0.652 | +0.161 |
| context_recall | 0.603 | 0.810 | +0.206 |
| **mean (4 metrics)** | **0.569** | **0.677** | **+0.108 (+19%)** |

By question type (mean of 4 metrics):

| anchor_type | n | baseline | treatment | delta |
|---|---:|---:|---:|---:|
| easy_retrieve | 10 | 0.565 | 0.725 | +0.160 |
| reasoning | 8 | 0.639 | 0.744 | +0.105 |
| should_refuse | 3 | 0.394 | 0.338 | −0.056 |

Latency (per question): baseline mean **20.4 s**; treatment **warm** mean **22.4 s**
(**+2.0 s/q, +10%**); treatment **cold** first query **50.2 s** (one-time FlashRank
ONNX load). Image size: the rerank dependencies are **~28 MB** (`llama_index` 28 MB +
`flashrank` 40 KB) on a **4.77 GB** image (~0.6%) and are identical in both arms — the
FlashRank reranker model is downloaded at runtime, not baked into the image.

## Decision

1. **Make hierarchical-rerank retrieval the default (default-ON).** It improves
   answer quality by **+0.108 mean RAGAS (+19%)**, driven by large gains in
   context_recall (+0.206) and context_precision (+0.161) — i.e. it retrieves the
   right context far more often — with answer_relevancy essentially flat (−0.025).
   The cost is modest: **+2.0 s/q (+10%)** warm latency, a one-time ~50 s cold load
   per deployment, and negligible image size.

2. **Keep the parent/child chunk-size defaults: `parent_chunk_size: 2048`,
   `child_chunk_size: 512`.** These produced the +0.108 win in this run. A dedicated
   size sweep (1024/256, 2048/512, 4096/512) is deferred (tasks 5.4) — recommend
   2048/512 until that runs.

3. **Keep `bm25_weight: 0.6`** (semantic 0.4). Held fixed across both arms here; a
   weight sweep is deferred (task 5.5).

## Consequences

- Flip the shipped default for `data_manager.retrievers.hierarchical_rerank.enabled`
  to `true` in a follow-up (kept default-off in code for this evaluation; this ADR
  records the recommendation — the toggle flip is its own change).
- **`should_refuse` regressed −0.056 (n=3):** richer retrieved context makes the model
  slightly less likely to refuse out-of-scope questions. Small sample; **monitor** and
  re-check on a larger refuse set before relying on it.
- The first query after each (re)deploy pays the ~50 s FlashRank load — warm/discard it
  in latency-sensitive contexts.

## Caveats

- **Ground-truth is DRAFT.** The 21 reference answers carry `DRAFT` notes and some
  encode drift-prone FASRC facts (operator confirmation tracked as a HIGH-priority
  Asana task; OpenSpec task 1.3, out of scope here). Both arms are graded against the
  **same** bank, so the **relative delta (treatment > baseline) is robust**; the
  **absolute** scores are provisional until the ground truth is locked.
- The SUT (Qwen) emitted some chain-of-thought bleed (`</think>`) because the benchmark
  SUT config does not set `enable_thinking: false`; this affects **both arms equally**
  and does not change the comparison.
