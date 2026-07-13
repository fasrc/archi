## 1. Preconditions — make the result interpretable (D0, D7)

- [ ] 1.1 Nuke + full re-ingest the dev corpus (a plain re-ingest will NOT refresh #97's sliced bodies — `persist_resource` skips existing files); verify with the deploy-verify smoke test
- [ ] 1.2 Report category coverage: fraction of KB chunks with a non-empty `metadata.category`, plus the distribution across the 19 labels. **HARD GATE** — if coverage is low, the oracle is inert and a null result would measure the extractor, not the idea. Fix extraction or scope the experiment to the covered subset before proceeding
- [ ] 1.3 Add ~5 questions to `examples/benchmarking/fasrc_ragas_queries.json` whose gold source is a `slurm.schedmd.com` page, closing the non-KB blind spot (today: zero non-KB gold sources in any bank)
- [ ] 1.4 Confirm the expanded bank still passes `benchmark-bank-preflight`

## 2. The rerank seam (D1)

- [ ] 2.1 RED: test asserting `LlamaIndexHierarchicalRetriever._adjust_ranked_scores` is identity — same `ranked` list in, same list out, so production ordering is bit-for-bit unchanged
- [ ] 2.2 GREEN: add the `_adjust_ranked_scores(query, candidates, ranked)` hook (identity default) and call it where `ranked` is produced (`hierarchical_retriever.py:236`)
- [ ] 2.3 Test that a subclass overriding the hook actually changes the returned document order — proving the seam can promote a candidate from below the top-k cut, which post-hoc reordering of the returned top-5 could not

## 3. Retrieval-only scorer (D2, D3, D4)

- [ ] 3.1 RED: tests for the scoring primitives against a fake retriever — hit-rate@k, MRR, and exclusion of zero-source (`should_refuse`) rows from both
- [ ] 3.2 GREEN: implement hit-rate@k + MRR over a question bank, matching returned document URLs against each question's `sources`
- [ ] 3.3 Build the `url -> category` map in one query over `documents` ⋈ `document_chunks`; expose `gold_category(q)` as the category of the article at `q.sources[0]`
- [ ] 3.4 Wire the scorer to the deployment's configured retriever via `build_vector_retriever` (`retrievers/factory.py:29`) using the project's own config loading — no hand-read secrets, no LLM construction
- [ ] 3.5 Verify the scorer needs no LLM credentials: it completes with no API keys in the environment

## 4. Counter-metrics — without these a boost result is inadmissible

- [ ] 4.1 RED + GREEN: `non_kb_share@k` — fraction of returned top-k that is not under `docs.rc.fas.harvard.edu/kb/` (exactly the population that can never carry a category, hence can never be boosted)
- [ ] 4.2 RED + GREEN: `refusal_confidence` — top-k rerank scores (max and mean) returned for `should_refuse` anchors, so a boost that manufactures confident context for out-of-scope questions is visible
- [ ] 4.3 Emit both counter-metrics in the same result record as hit-rate@k and MRR — never separately

## 5. Oracle boost + sweep (D5, D6)

- [ ] 5.1 RED: test that the oracle boost boosts every chunk sharing the gold article's *category*, not the gold article itself — it must simulate a perfect classifier, not circularly retrieve the answer
- [ ] 5.2 GREEN: benchmark-only retriever subclass overriding `_adjust_ranked_scores` to apply `score' = rerank_score + w · (chunk.category == gold_category(q))`
- [ ] 5.3 Sweep `w` across a range in a single run against one corpus (FlashRank's score scale is model-dependent — `w` cannot be chosen a priori), recording all four metrics at each `w`
- [ ] 5.4 Assert production is untouched: with no oracle configured, retrieval results are identical to baseline

## 6. Measure and decide

- [ ] 6.1 Record **baseline**: hit-rate@k, MRR, `non_kb_share@k`, `refusal_confidence` on the re-ingested corpus
- [ ] 6.2 Record the **oracle sweep** across `w`; identify the ceiling
- [ ] 6.3 Apply the decision gate and write the verdict into the change:
  - Δ≈0 ⇒ **kill the boost**, keep the harness. No classifier is built.
  - Δ large, `non_kb_share` stable, `refusal_confidence` stable ⇒ provisional ceiling; expand the bank before tuning `w` or choosing a classifier
  - Δ large but `non_kb_share` collapses ⇒ the lift is an artifact of demoting SchedMD docs; restrict to within-KB or drop
  - Δ large but `refusal_confidence` rises ⇒ the boost is degrading refusal; treat as a regression, not a win
- [ ] 6.4 State the asymmetry explicitly in the write-up: a **null** result is decisive (perfect mapping + favorable bank + no lift ⇒ no classifier can help); a **positive** result is provisional, because 18 gold-sourced questions across ~7 articles — two of which dominate — cannot support tuning

## 7. Gate and land

- [ ] 7.1 `bash scripts/gate.sh` green (format → lint → test, ≥80% diff coverage)
- [ ] 7.2 Update `docs/` for the new benchmark (how to run it, what the counter-metrics mean, and why a positive result is not a green light)
- [ ] 7.3 Open PR to `fasrc/archi --base dev`; request `@codex review`
