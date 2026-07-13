## 1. Preconditions — make the result interpretable (D0, D7)

- [ ] 1.1 Nuke + full re-ingest the dev corpus (a plain re-ingest will NOT refresh #97's sliced bodies — `persist_resource` skips existing files); verify with the deploy-verify smoke test
- [ ] 1.2 Report category coverage: fraction of KB chunks with a non-empty `metadata.category`, plus the distribution across the 19 labels. **HARD GATE** — if coverage is low, the oracle is inert and a null result would measure the extractor, not the idea. Fix extraction or scope the experiment to the covered subset before proceeding
- [ ] 1.3 Add ~5 questions to `examples/benchmarking/fasrc_ragas_queries.json` whose gold source is a `slurm.schedmd.com` page, and at least 1 answered by the namesake wiki page — the corpus has three source groups, and today zero non-KB gold sources exist in any bank. Use the modern dialect (`user_input`/`reference`/`sources`)
- [ ] 1.4 Add an authored `assumed_category` to **every** question in the bank — the in-KB category a plausible classifier would assign — including `should_refuse` anchors and the new non-KB rows. Record the reasoning in `notes` so the label is auditable (D3a). Without this the harm channels are unmeasurable
- [ ] 1.5 Confirm the expanded bank still passes `benchmark-bank-preflight`

## 2. The rerank seam (D1)

- [ ] 2.1 RED: test asserting `LlamaIndexHierarchicalRetriever._adjust_ranked_scores` is identity — same `ranked` list in, same list out, so production ordering is bit-for-bit unchanged
- [ ] 2.2 GREEN: add the `_adjust_ranked_scores(query, candidates, ranked)` hook (identity default) and call it where `ranked` is produced (`hierarchical_retriever.py:236`)
- [ ] 2.3 Test that a subclass overriding the hook actually changes the returned document order — proving the seam can promote a candidate from below the top-k cut, which post-hoc reordering of the returned top-5 could not

## 3. Retrieval-only scorer (D2, D3, D4)

- [ ] 3.1 RED: tests for the scoring primitives against a fake retriever — hit-rate@k, MRR, and exclusion of zero-source (`should_refuse`) rows from both
- [ ] 3.2 GREEN: implement hit-rate@k + MRR over a question bank, matching returned document URLs against each question's `sources`
- [ ] 3.3 RED + GREEN: URL canonicalization applied to **both** sides before matching and before the category join (trailing slashes, slug variants) — the bank README explicitly warns SOURCES mode needs URL reconciliation. Report any gold source that resolves to no ingested document as **unresolved**, never as a silent miss
- [ ] 3.4 Build the `url -> category` map in one query over `documents` ⋈ `document_chunks`; expose `gold_category(q)` as the canonicalized-URL lookup of `q.sources[0]`
- [ ] 3.5 Wire the scorer to the deployment's configured retriever via `build_vector_retriever` (`retrievers/factory.py:29`) using the project's own config loading — no hand-read secrets, no answer-generation LLM
- [ ] 3.6 Verify the scorer needs no **answer-generation** credentials on a local-embedding deployment (dev uses `HuggingFaceEmbeddings`), and document that a hosted embedder still requires its own key — the no-key guarantee is scoped, not blanket

## 4. Counter-metrics — without these a boost result is inadmissible

- [ ] 4.1 RED + GREEN: `non_kb_share@k` — fraction of returned top-k not under `docs.rc.fas.harvard.edu/kb/` (exactly the population that can never carry a category, hence can never be boosted)
- [ ] 4.2 RED + GREEN: `refusal_confidence` — top-k rerank scores (max and mean) returned for `should_refuse` anchors
- [ ] 4.3 Emit both counter-metrics in the same result record as hit-rate@k and MRR — never separately
- [ ] 4.4 Assert both counter-metrics are read from a **simulated-classifier** run and are reported as *vacuous* if taken from an oracle run, where the at-risk rows carry no category and are never boosted

## 5. Treatment modes: oracle (benefit) and simulated-classifier (harm) — D3a, D5, D6

- [ ] 5.1 RED: test that the oracle boost boosts every chunk sharing the gold article's *category*, not the gold article itself — it must simulate a perfect classifier, not circularly retrieve the answer
- [ ] 5.2 GREEN: benchmark-only retriever subclass overriding `_adjust_ranked_scores` to apply `score' = rerank_score + w · (chunk.category == treatment_category(q))`
- [ ] 5.3 RED + GREEN: **oracle mode** — `treatment_category(q)` = gold article's captured category. Measures the ceiling only. Rows with no gold source or no captured category get no boost (documented as benefit-only, not safe)
- [ ] 5.4 RED + GREEN: **simulated-classifier mode** — `treatment_category(q)` = the authored `assumed_category`, applied to every row including refusal anchors and non-KB rows. This is the mode that can express harm
- [ ] 5.5 Sweep `w` across a range in a single run per mode (FlashRank's score scale is model-dependent — `w` cannot be chosen a priori), recording all four metrics at each `w`
- [ ] 5.6 Assert production is untouched: with no treatment configured, retrieval results are identical to baseline

## 6. Measure and decide

- [ ] 6.1 Record **baseline**: hit-rate@k, MRR, `non_kb_share@k`, `refusal_confidence` on the re-ingested corpus
- [ ] 6.2 Record the **oracle sweep** across `w` → the ceiling (benefit upper bound)
- [ ] 6.3 Record the **simulated-classifier sweep** across `w` → the harm channels
- [ ] 6.4 Apply the decision gate and write the verdict into the change:
  - Δ(oracle)≈0 ⇒ **kill the boost**, keep the harness. No classifier is built
  - Δ(oracle) large, and simulated-classifier run shows `non_kb_share` + `refusal_confidence` stable ⇒ provisional ceiling; expand the bank before tuning `w` or choosing a classifier
  - Δ(oracle) large but simulated-classifier `non_kb_share` collapses ⇒ lift is an artifact of demoting SchedMD/wiki docs; restrict to within-KB or drop
  - Δ(oracle) large but simulated-classifier `refusal_confidence` rises ⇒ the boost degrades refusal; treat as a regression, not a win
- [ ] 6.5 State the asymmetry explicitly in the write-up: a **null** is decisive (perfect mapping + favorable bank + no lift ⇒ no classifier can help); a **positive** is provisional, because 18 gold-sourced questions across ~7 articles — two of which dominate — cannot support tuning. Benefit and harm come from *different runs*; never report an oracle-run counter-metric as a safety result

## 7. Gate and land

- [ ] 7.1 `bash scripts/gate.sh` green (format → lint → test, ≥80% diff coverage)
- [ ] 7.2 Update `docs/` for the new benchmark: how to run each mode, what the counter-metrics mean, why the oracle cannot show harm, and why a positive result is not a green light
- [ ] 7.3 Open PR to `fasrc/archi --base dev`; request `@codex review`
