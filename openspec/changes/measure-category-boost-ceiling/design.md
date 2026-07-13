## Context

PR #97 (merged, `17b2079d`) added `HtmlCategoryProcessor`: the Echo-KB breadcrumb
(`span.eckb-breadcrumb-link`) becomes `metadata["category"]`, which rides to every chunk via
the existing merge (`manager.py:561` flat / `:835` hierarchical). **Nothing reads it.**
`llm_category` (the 6-label ingest-time LLM guess) is likewise write-only — zero read sites
repo-wide.

The live retrieval path is **FASRCDocsAgent only** (`QAPipeline` is deprecated;
`GradingPipeline` is a rubric grader; `CMSCompOpsAgent` is a different deployment). It is
also the sole caller of `build_vector_retriever` (`fasrc_docs_agent.py:216`) and therefore
the only path with a cross-encoder rerank stage. Retrieval is three stages
(`hierarchical_retriever.py:220-281`): `hybrid_search` → 20-candidate pool → FlashRank
`_rerank` → parent dedupe → top-5.

The existing `retrieval-benchmarking` capability cannot answer our question: it is RAGAS-based
and **deploy-per-arm** (each arm = deploy + ingest + evaluate, with LLM secrets). Sweeping a
scoring weight through it would mean one full redeploy per weight.

The question bank is `examples/benchmarking/fasrc_ragas_queries.json`: **10 `easy_retrieve` +
8 `reasoning`** (gold-sourced) **+ 3 `should_refuse`** (no gold source *by design* — they test
refusal, not recall). `service_benchmark.py:910` already handles zero-source rows.

## Goals / Non-Goals

**Goals:**
- Decide the category-boost feature on a measured number, before building a classifier.
- A durable, LLM-free retrieval scorer that can sweep a scoring parameter against one corpus
  in one process — no redeploy, no re-ingest, no LLM credentials.
- Make both harm channels *visible*, since the current bank is blind to both.
- Leave production retrieval behaviorally unchanged.

**Non-Goals:**
- Wiring the soft boost into production (gated on the decision this change produces).
- Choosing between the classifier candidates (embedding-affinity centroids vs. agent tool
  argument — both viable now that `QAPipeline` is dead).
- Any hard metadata filter. The dormant `filter` plumbing at `postgres_vectorstore.py:350`
  stays dormant — and note it interpolates the filter **key** into SQL
  (`f"c.metadata->>'{key}'"`), an injection surface the moment a key becomes model-chosen.
- Touching `src/bin/service_benchmark.py`; the RAGAS path is unchanged.

## Decisions

### D0 — Verify category coverage FIRST; it is a precondition for the result to mean anything
A null oracle result is only decisive **if the corpus is actually categorized**. If #97's
breadcrumb extractor tagged only a minority of KB articles, the oracle boost is largely inert,
and Δ≈0 would measure a weak *extractor*, not a bad *idea* — a confound that would silently
invalidate the whole experiment.

So before any sweep: report the fraction of KB chunks carrying a non-empty `category`, and the
distribution across the 19 labels. If coverage is low, fix extraction (or scope the experiment
to the covered subset) before drawing any conclusion. This is a hard gate, not a nice-to-have.

### D1 — An identity hook at the rerank seam, subclassed by the benchmark
The boost **must act on the full 20-candidate pool before truncation**. Boosting the returned
top-5 could only reorder them — it could never promote a document from rank 12 into the top 5,
which is precisely the effect being measured. So the adjustment has to happen inside
`_get_relevant_documents`, between `_rerank` and the parent dedupe.

Add one overridable hook to `LlamaIndexHierarchicalRetriever`, defaulting to identity:

```python
def _adjust_ranked_scores(self, query, candidates, ranked):
    """Hook: re-score/re-order `ranked`. Base implementation is identity."""
    return ranked
```

and call it where `ranked` is produced (`:236`):

```python
ranked = self._adjust_ranked_scores(query, candidates, self._rerank(query, candidates))
```

Production behavior is **bit-for-bit unchanged** (identity). The benchmark subclasses the
retriever and overrides only this hook. Rejected alternatives: copying the whole
`_get_relevant_documents` into a benchmark subclass (duplicates 60 lines and drifts);
monkeypatching `_rerank` (fragile, and `_rerank` lacks the candidate metadata the boost needs
in a natural shape).

Bonus: this is *exactly* the seam a real soft boost would later occupy, so the experiment
validates the production seam rather than testing something else.

`hierarchical_retriever.py` is black/isort-clean (tree normalized in #69), and the change is a
new method plus one rewritten line — low churn against the ≥80% diff-coverage gate.

### D2 — Retrieval-only scorer, reusing the deployment's own retriever
New script under `scripts/benchmarking/`. It builds the *configured* retriever via
`build_vector_retriever` (`retrievers/factory.py:29`) against the live vectorstore, using the
project's own config loading — **not** hand-read secrets. For each question it invokes the
retriever and scores the returned documents' URLs against `sources`.

No LLM is constructed, so no LLM credentials are needed. This is the whole reason the sweep is
cheap: one process, one corpus, N weights.

### D3 — Gold category by URL join, computed once
`gold_category(q)` = the captured `category` of the article at `q.sources[0]`. Build a
`url -> category` map in one query over `documents` ⋈ `document_chunks` at startup, then look
up per question. No hand-labeling anywhere.

The oracle boosts **every chunk sharing the gold article's category**, not the gold article
itself — that is what makes it a simulation of a *perfect classifier* rather than a circular
lookup of the answer.

### D4 — KB membership by URL prefix
A document is a KB page iff its URL is under `docs.rc.fas.harvard.edu/kb/`. This is the same
population that can carry a breadcrumb, so it exactly partitions "can ever be boosted" from
"can never be boosted" — which is what `non_kb_share@k` needs to mean.

### D5 — `refusal_confidence` = retrieval score mass on `should_refuse` anchors
For each `should_refuse` question, record the top-k rerank scores (report max and mean). The
metric is a *comparison*, not an absolute: if the boosted arm returns systematically
higher-scoring context than baseline for questions the assistant should decline, the boost is
degrading refusal — it is manufacturing plausible context for an out-of-scope question.

### D6 — Sweep `w`, never assume it
The boost is added to a **FlashRank cross-encoder score**, not a cosine similarity, and that
scale is model-dependent (`ms-marco-MiniLM-L-12-v2` by default). `w` therefore cannot be
reasoned about a priori. Report the full sweep; read the ceiling off it.

### D7 — Close the bank's blind spot before trusting any number
Every gold source in every bank is a KB page — **zero** in `slurm.schedmd.com`. The set is
structurally incapable of observing non-KB demotion, so an unfixed bank would let the oracle
flatter itself. Add ~5 questions whose gold source is a SchedMD page. New questions must keep
passing `benchmark-bank-preflight`.

## Risks / Trade-offs

- **The bank is thin.** 18 gold-sourced questions hit only ~7 distinct articles, and
  `running-jobs` + `cluster-storage` carry 5 each — two articles dominate the signal. This
  forces the asymmetric reading: a **null** is decisive (a perfect mapping on a favorable bank
  showing nothing kills the idea), a **positive** is provisional (requires bank expansion
  before tuning `w` or choosing a classifier). Reporting must say so explicitly, or a weak
  positive will be misread as a mandate.
- **Coverage confound (D0).** Mitigated by making coverage a hard precondition; without it a
  null is uninterpretable.
- **The oracle is optimistic by construction.** It resolves every ambiguous query to the "right"
  category by definition. That is intended — it is an upper bound — but it means the real
  classifier will land *below* whatever the sweep shows, never above.
- **Requires a nuke + full re-ingest of dev.** A plain re-ingest will **not** refresh #97's
  sliced bodies: `_handle_standard_url` calls `persist_resource` without `overwrite`, and
  persistence skips files that already exist. The dev deploy-verify already hit this.
- **The identity hook is production code.** It is behavior-neutral, but it is still a diff on
  the live retrieval path and must carry a test proving the base implementation is identity.
- **Measuring against a live shared deployment.** The scorer is read-only, but it runs against
  the dev corpus; runs should be coordinated so a concurrent re-ingest doesn't shift the corpus
  mid-sweep.
