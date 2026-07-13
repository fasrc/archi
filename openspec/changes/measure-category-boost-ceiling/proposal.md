## Why

PR #97 landed website-category capture: every FASRC KB chunk now carries
`metadata["category"]` (19 site taxonomy labels, from the Echo-KB breadcrumb). Nothing
reads it. The obvious next step is a retrieval soft boost —
`score' = rerank_score + w · (chunk.category == query.category)` — but that requires
building a query→category classifier, and there is good reason to doubt the whole feature
pays for itself:

- **Category is largely a lossy function of the query's own words.** "Where do I store big
  datasets?" → `Storage`; BM25 and the embedder already keyed on "store."
- **It is anti-correlated with need.** It would help most where query vocabulary does *not*
  match the target article — exactly where a classifier, reading the same words, also fails.
- **A cross-encoder already does this, continuously.** FASRCDocsAgent reranks with FlashRank
  using full query–passage attention; a 19-bucket match is a coarse approximation of it.

We cannot answer this by argument, and we cannot answer it with the existing benchmark:
`retrieval-benchmarking` is RAGAS-based and **deploy-per-arm** (each arm = deploy + ingest +
evaluate, with LLM secrets), which is far too heavy to sweep a scoring weight.

So: **measure the ceiling before building the machine.** Feed the boost a *perfect* (oracle)
query→category mapping. If a perfect mapping does not beat baseline, no real classifier will,
and we drop the feature having spent a script instead of a subsystem.

The labels are free. `fasrc_ragas_queries.json` questions carry gold source URLs (the
human-verified answer location); #97 gives article → category. Compose them and you get
query → true category with zero hand-labeling.

## What Changes

- **New: a retrieval-only benchmark.** Scores retrieval directly — hit-rate@k and MRR against
  each question's gold sources — with **no LLM call and no answer generation**, so it runs
  in-process against a live corpus and can sweep a scoring parameter without a redeploy per arm.
- **New: two mandatory counter-metrics**, reported with every run. A boost result is
  inadmissible without them, because the current bank is structurally blind to both harms the
  boost risks:
  - `non_kb_share@k` — fraction of top-k that is not a KB page. Only KB pages have a
    breadcrumb, so slurm/wiki chunks have no category *permanently*. A boost they can never
    receive is arithmetically a **penalty on the entire non-KB corpus**.
  - `refusal_confidence` on `should_refuse` anchors — top-k retrieval scores for out-of-scope
    questions. A classifier will happily map *"GPU partition layout on MIT's Engaging cluster"*
    to `Cluster Usage`; the boost then promotes confident FASRC context for a question the bot
    should decline. **The boost can manufacture false confidence**, turning refusals into
    confident wrong answers.
- **New: an oracle boost mode** in the harness (experiment-only, never production) that applies
  `w · (chunk.category == gold_category(q))` at the rerank seam and sweeps `w`. FlashRank's
  score scale is model-dependent, so `w` cannot be reasoned about a priori — it must be swept.
- **Modified: the question bank must cover non-KB gold sources.** Today every gold source in
  every bank is a KB page — **zero** live in `slurm.schedmd.com` — so the set cannot detect
  non-KB demotion. Add ~5 slurm-answered questions. Until this lands, no boost measurement is
  trustworthy.
- **A decision gate, not a feature.** This change deliberately ships *no* production retrieval
  behavior. Its output is a number and a decision.

## Capabilities

### New Capabilities
- `retrieval-only-benchmark`: LLM-free, in-process scoring of retrieval quality against gold
  sources (hit-rate@k, MRR), with the `non_kb_share@k` and `refusal_confidence` counter-metrics
  and an experiment-only oracle category-boost sweep.

### Modified Capabilities
- `retrieval-benchmarking`: the "Grounded FASRC question banks" requirement gains a coverage
  obligation — a bank must include gold sources from **every corpus source group** (KB *and*
  non-KB), so that a treatment which harms one group is detectable rather than invisible.

## Impact

- **New code:** a retrieval-only scorer under `scripts/benchmarking/`. Reuses
  `build_vector_retriever` (`retrievers/factory.py:29`) and the project's own config loading;
  does **not** touch `src/bin/service_benchmark.py` (RAGAS path is unchanged).
- **Data:** `examples/benchmarking/fasrc_ragas_queries.json` gains ~5 slurm-gold questions.
  Current bank is 10 `easy_retrieve` + 8 `reasoning` (gold-sourced) + 3 `should_refuse` (no
  gold source, by design — they test refusal, not recall).
- **Production retrieval: unchanged.** The soft boost is *not* wired in. The oracle lives in
  the harness only.
- **Operational precondition:** measuring against dev needs a **nuke + full re-ingest** — a
  plain re-ingest will not refresh #97's sliced bodies, because `persist_resource` skips files
  that already exist.
- **Deliberately out of scope:** wiring the boost into production (gated on the decision);
  choosing between the two classifier candidates (embedding-affinity centroids vs. an agent
  tool argument — both viable now that `QAPipeline` is deprecated); any hard metadata filter
  (the dormant `filter` plumbing at `postgres_vectorstore.py:350` stays dormant, and note it
  interpolates the filter **key** into SQL — an injection surface the moment a key becomes
  model-chosen).
- **No new third-party dependencies.**
