# Implementation Plan — Feature 1: Per-Source Retrieval Relevance (Agent Path)

> Planning artifact only. No code has been changed. Companion to
> `notes_response_tuning.md` (the exploration + locked decisions).

---

## Context

**Problem.** The FASRC Cannon support bot (ReAct agent, `CMSCompOpsAgent`)
retrieves documents whose hybrid relevance score is computed by the retriever
and shown to the *model*, but the score is discarded before it reaches the
chat UI (`retriever.py:116` passes only `Document` objects; `RunMemory` stores
no scores — see its `# TODO` at line 14). The agent's final
`PipelineOutput.metadata` therefore never contains `retriever_scores`, so the
existing sources box renders agent answers with **no relevance signal and no
score-driven ordering**.

**What prompted this.** While debugging a search-loop regression in the FASRC
bot we explored surfacing how relevant each cited document is. Investigation
(two grounded Explore passes + pyright) confirmed the score exists end-to-end
in the retriever but is dropped at a single, well-defined seam, and that the
shared renderer (`get_top_sources`) is mis-oriented for the hybrid metric.

**Intended outcome.** Each source under an agent answer shows a relevance
bucket plus the raw normalized number, e.g.:

```
Sources
• FASRC GPU Partitions    — Strong (0.82)
• SLURM Submission Guide  — Partial (0.61)
• Storage Quotas FAQ      — Weak (0.34)
```

ordered best-first, with **zero behavioral or display change to the
classic-QA path**.

---

## Locked Decisions (from the walkthrough)

| # | Decision |
|---|----------|
| Scope | **Feature 1 only**, phased. Per-source relevance. No prompt changes. |
| Accuracy | = the retriever's hybrid relevance score (not model self-confidence). |
| Presentation | **Bucket + raw** — `Strong/Partial/Weak (0.NN)`, reuse existing sources box. |
| Q3 normalization | Normalize hybrid `combined_score` → **0–1 at the agent boundary**; do **not** reuse the legacy lower-is-better `similarity_score_reference` logic for the agent path. |
| Q3 fix scope | **Agent path only now**; classic-QA's pre-existing mis-ordering is **left as-is but documented as an explicit follow-up** (not silently ignored). |
| Q4 prompt | Simplified prompt is a **separate A/B track** — out of scope here. |
| Feature 2 | Inline per-claim `(ref: file.html)` tags **deferred** to a robust pipeline-driven phase. Out of scope here. |

---

## Why the agent path can be isolated cleanly

- **Classic-QA** (`qa.py:89`) calls `self.retriever.invoke()` (HybridRetriever)
  directly, unpacks `(doc, score)`, and sets `metadata["retriever_scores"]`
  itself (`qa.py:112`). It does **not** go through the agent retriever tool.
- **Agent** retrieval flows through `retriever.py` `_retriever_tool` →
  `_normalize_results` (already yields `(Document, Optional[float])`) →
  `store_docs` → `RunMemory` → `base_react.finalize_output`.

So `retriever.py` and `RunMemory`/`finalize_output` are an **agent-only seam**.
Normalizing there cannot affect classic-QA. The only shared component is the
renderer (`app.py` `get_top_sources` / `_format_source_entry`), so isolation
there is achieved with a **metadata flag emitted only by the agent path**.

---

## Recommended Approach

Carry the score inside `Document.metadata` (least-invasive: no callback
signature churn, no `RunMemory` storage rewrite, no `local_files.py` ripple),
normalize at the agent boundary, and gate the renderer change on a
metadata flag.

### Step 1 — Normalize + stamp the score at the agent boundary
**File:** `src/archi/pipelines/agents/tools/retriever.py` (~lines 113–117)

- After `docs = _normalize_results(results or [])` (already
  `Sequence[Tuple[Document, Optional[float]]]`), compute a normalized
  relevance in `[0,1]` and stamp it onto each document:
  `doc.metadata["retriever_score"] = relevance`.
- `store_docs(...)` call stays exactly as-is (still passes `Document`s) — the
  score now rides along in metadata. **No signature changes anywhere.**
- Add a small normalization helper (new util, e.g.
  `src/archi/pipelines/agents/utils/relevance.py`):
  `normalize_relevance(combined_score: float) -> float`.
  - **v1 rule:** `clamp(combined_score, 0.0, 1.0)`.
  - **Documented assumption:** valid because configured weights are
    `semantic 0.4 / bm25 0.6` (sum ≈ 1) and `semantic_score = 1 - cosine_dist`
    with normalized embeddings keeps `combined_score` in roughly `[0,1]`.
    BM25 can exceed 1 in rare high-frequency cases → clamp is the safety net.
  - This single assumption is the **largest residual correctness risk** —
    explicitly validate it in the verification step (eyeball displayed
    relevance vs. match quality on real FASRC queries).

### Step 2 — Surface scores aligned to deduped documents
**File:** `src/archi/pipelines/agents/utils/run_memory.py`

- Add `unique_documents_with_scores() -> List[Tuple[Document, Optional[float]]]`
  that mirrors `unique_documents()` (same `_document_key` dedup) but returns
  each surviving doc paired with `doc.metadata.get("retriever_score")`.
  - On dedup collision keep the **max** score (best evidence wins).
- Leave `record`, `record_documents`, `unique_documents` **unchanged**
  (zero blast radius to their existing callers at `base_react.py:1117/1120/1673`).

### Step 3 — Emit scores + isolation flag from the agent
**File:** `src/archi/pipelines/agents/base_react.py` (`finalize_output`, ~107–128)

- Replace the `documents = memory.unique_documents()` line with
  `unique_documents_with_scores()`, splitting into a `documents` list and a
  parallel `scores` list (same order).
- Set `resolved_metadata["retriever_scores"] = scores` (reusing the exact key
  classic-QA already uses — `qa.py:112` — so downstream consumers need no
  changes).
- Set an isolation flag, e.g.
  `resolved_metadata["retriever_scores_normalized"] = True`.
- `_store_documents` (base_react.py:1109) needs **no change** (scores travel in
  metadata, not as a new argument).

### Step 4 — Similarity-aware, opt-in rendering
**File:** `src/interfaces/chat_app/app.py`

- `get_top_sources(documents, scores)` (447–492): add an opt-in
  `similarity_mode: bool = False`. When `True`:
  - sort **descending** (best-first) instead of `np.argsort` ascending,
  - **skip** the `score > self.similarity_score_reference` break (legacy
    distance threshold is meaningless for a 0–1 similarity),
  - keep the existing display-name dedup and `-1.0`/`None` sentinels.
- The shared call site (`app.py:1493`, `_finalize_result`) and the /v1
  streaming sites (`2014`, `2079`) pass
  `similarity_mode = bool(metadata.get("retriever_scores_normalized"))`.
  Classic-QA never sets the flag → its path is **byte-for-byte unchanged**.
- `_format_source_entry` (494–507): when in similarity_mode, render
  `— {bucket} ({score:.2f})`; otherwise the current `({score:.2f})` exactly
  as today (no classic-QA visual change).
- Add `_relevance_bucket(score: float) -> str` (Strong/Partial/Weak).

### Step 5 — Bucket thresholds config (with code defaults)
**File:** config under `data_manager.retrievers.hybrid_retriever`

- Optional `bucket_thresholds: { strong: 0.70, partial: 0.40 }`.
- Code ships these defaults so it works **without** editing the deployed
  config (prod config is a separate file synced via `archi deploy`).

### Step 6 — Incidental type-hint correction (required by gate)
**File:** `src/archi/pipelines/agents/tools/local_files.py` (lines 215, 333)

- `store_docs` param hints say `Sequence[Path]` but are actually called with
  `Sequence[Document]`, producing the **pre-existing** pyright errors at
  `cms_comp_ops_agent.py:204,213`. Correct hints to `Sequence[Document]`.
  Pure annotation fix; clears 2 baseline errors so the "no new pyright errors"
  gate is meaningful. (Not behavioral.)

### Step 7 — File the classic-QA follow-up (decision: documented, not ignored)
- Add a code comment + `TODO(feature2/classic-qa-ordering)` near
  `app.py:452` and a tracked issue/note: *classic-QA feeds the same
  higher-is-better hybrid score into the legacy lower-is-better
  `get_top_sources`, so its sources are ordered worst-first today.* Out of
  scope for Feature 1 by explicit decision; fix by routing classic-QA through
  the same `similarity_mode` flag in a follow-up.

---

## Critical Files

| File | Change |
|---|---|
| `src/archi/pipelines/agents/tools/retriever.py` | normalize + stamp `metadata["retriever_score"]` (~113–117) |
| `src/archi/pipelines/agents/utils/relevance.py` *(new)* | `normalize_relevance`, bucket helper |
| `src/archi/pipelines/agents/utils/run_memory.py` | add `unique_documents_with_scores()` |
| `src/archi/pipelines/agents/base_react.py` | `finalize_output`: scores + isolation flag (~107–128) |
| `src/interfaces/chat_app/app.py` | `get_top_sources` similarity_mode (447–492); `_format_source_entry` (494–507); call sites 1493/2014/2079 |
| `src/archi/pipelines/agents/tools/local_files.py` | type-hint fix (215, 333) |
| config `data_manager.retrievers.hybrid_retriever` | optional `bucket_thresholds` |

## Reuse (don't reinvent)

- `retriever.py:_normalize_results` (15–29) already yields `(Document, score)` —
  do **not** re-parse retriever output.
- `metadata["retriever_scores"]` key + parallel-list contract already defined
  by classic-QA (`qa.py:90–112`) and consumed by `app.py:1493`,
  `openai_compat.py:269/333`, `app.py:2014/2079`. Match it exactly.
- `RunMemory._document_key` (219) dedup — reuse verbatim for score alignment.
- Existing `get_top_sources` dedup/sentinel logic — keep; only add the
  similarity branch.

---

## Verification (end-to-end)

1. **Type gate (LSP pyright).** Baseline before changes: `retriever.py` 1
   (unresolved `langchain.tools` import), `run_memory.py` 0, `base_react.py`
   24 (missing imports/optional-access), `cms_comp_ops_agent.py` 6 (2 of which
   = the store_docs hint, cleared by Step 6). Gate: **no new errors vs.
   baseline**; the 2 store_docs errors should disappear.
2. **Unit tests** (new): `normalize_relevance` clamping; `_relevance_bucket`
   boundaries; `unique_documents_with_scores` dedup keeps max score & order;
   `get_top_sources(similarity_mode=True)` sorts descending and ignores the
   legacy threshold; classic path (`similarity_mode=False`) output unchanged
   (golden test).
3. **Deploy** (per project workflow): `archi deploy` to sync code into
   `~/.archi/`, then `docker restart chatbot-archi-openai-compat`
   (conda env `archi-openai-compat-2`).
4. **Live check.** Ask the FASRC bot a real Cannon question; confirm the
   sources box shows `— Strong/Partial/Weak (0.NN)`, best-first ordering, and
   that the displayed relevance tracks eyeballed match quality (validates the
   Step-1 normalization assumption — the key risk).
5. **Regression.** Run a classic-QA query; confirm its sources render
   **identically to before** (no flag → unchanged path).
6. **/v1.** Hit the OpenAI-compat endpoint; confirm `retriever_scores` appear
   in citations for agent answers (previously empty).

---

## Out of Scope (explicit)

- Simplified/clarifying prompt rewrite — separate A/B agent track (Q4).
- Inline per-claim `(ref: file.html)` attribution — deferred Feature 2
  (robust pipeline-driven sentence→chunk mapping).
- Fixing classic-QA ordering — documented follow-up only (Step 7).

## Residual Risks

- **Normalization fidelity (highest).** `clamp(combined, 0, 1)` assumes
  weights ≈ sum 1 and BM25 ≈ normalized. If real FASRC scores cluster oddly,
  buckets may misclassify. Mitigation: thresholds are config-tunable; validate
  on real queries (verification step 4) before trusting the labels.
- **Dedup/score alignment.** If `_document_key` collapses docs with divergent
  scores, "max wins" could overstate a weak chunk. Acceptable for v1; revisit
  with Feature 2's chunk-level attribution.
- **Shared renderer.** `similarity_mode` defaults `False`; any missed call
  site simply yields today's behavior (safe-degrading, not a new bug).
