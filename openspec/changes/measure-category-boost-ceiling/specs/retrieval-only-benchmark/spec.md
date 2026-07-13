## ADDED Requirements

### Requirement: LLM-free retrieval scoring against gold sources

The system SHALL provide a benchmark that scores retrieval quality directly, without any
LLM call or answer-generation step. For each question carrying gold sources (the
human-verified URL(s) where the answer lives), it SHALL retrieve the top-k documents via
the deployment's configured retriever and report **hit-rate@k** (was any gold source
retrieved) and **MRR** (reciprocal rank of the first gold source).

Questions with no gold sources (`should_refuse` anchors) SHALL be excluded from hit-rate
and MRR rather than scored as misses, since for those the correct behavior is to retrieve
nothing useful.

Because it makes no LLM call, the benchmark SHALL run in-process against a live corpus and
SHALL NOT require a redeploy, a re-ingest, or LLM credentials to vary a retrieval scoring
parameter.

#### Scenario: Gold-sourced question is scored on rank

- **WHEN** a question carrying gold sources is run through the configured retriever
- **THEN** hit-rate@k reflects whether any gold source appears in the top-k, and MRR reflects the rank of the first one

#### Scenario: Refusal anchors are excluded from recall metrics

- **WHEN** a `should_refuse` question (no gold sources, by design) is run
- **THEN** it contributes to neither hit-rate@k nor MRR, and its exclusion is reported rather than silently applied

#### Scenario: No LLM credentials required

- **WHEN** the benchmark runs with no LLM API keys present in the environment
- **THEN** it completes and reports its metrics, because no answer is generated

#### Scenario: A scoring parameter is swept without redeploying

- **WHEN** the operator sweeps a retrieval scoring weight across several values
- **THEN** every value is measured against the same already-ingested corpus in one run, with no deploy or ingest between values

### Requirement: Counter-metrics reported with every run

The benchmark SHALL report two counter-metrics alongside hit-rate@k and MRR on every run, and
a treatment result SHALL be considered inadmissible if reported without them. This is because
a retrieval treatment can improve headline recall while silently harming a subset of the
corpus or a class of question.

- **`non_kb_share@k`** — the fraction of returned top-k documents that are not FASRC KB
  pages. Only KB pages carry an Echo-KB breadcrumb, so non-KB documents (e.g.
  `slurm.schedmd.com`) have no category permanently and can never receive a
  category-matched boost. A boost a document can never receive is arithmetically a penalty
  on that document.
- **`refusal_confidence`** — the top-k retrieval scores returned for `should_refuse`
  anchors. An out-of-scope question can still look on-topic ("GPU partition layout on MIT's
  Engaging cluster" reads as a cluster-usage question), so a category treatment may promote
  confident in-corpus context for a question the assistant should decline.

#### Scenario: Non-KB documents are displaced by a treatment

- **WHEN** a treatment raises hit-rate@k but `non_kb_share@k` falls materially against baseline
- **THEN** the run is reported as a suspected non-KB demotion artifact rather than as a clean improvement

#### Scenario: A treatment inflates confidence on out-of-scope questions

- **WHEN** a treatment raises the retrieval scores returned for `should_refuse` anchors against baseline
- **THEN** the run is reported as degrading refusal, because higher-scoring in-corpus context makes a fabricated answer more likely

#### Scenario: Counter-metrics accompany the headline number

- **WHEN** any benchmark run completes
- **THEN** `non_kb_share@k` and `refusal_confidence` are emitted together with hit-rate@k and MRR in the same result record

### Requirement: Oracle category-boost sweep for ceiling measurement

The benchmark SHALL support an experiment-only **oracle** boost that applies a category match
using the true category of the question's gold article, so that the value of category-aware
retrieval can be measured *before* any query→category classifier is built:

```
score' = rerank_score + w · (chunk.category == gold_category(q))
```

where `gold_category(q)` is derived by joining the question's gold source URL to that
article's captured `metadata["category"]` — yielding query→category labels with no
hand-labeling. The oracle boosts the gold article's *category*, not the gold article
itself, so it simulates a perfect classifier rather than trivially retrieving the answer.

The boost weight `w` SHALL be swept rather than fixed, because it is added to a
cross-encoder score whose scale is model-dependent and therefore cannot be chosen a priori.

This capability SHALL exist only in the benchmark. It SHALL NOT alter production retrieval.

#### Scenario: Oracle labels are derived without hand-labeling

- **WHEN** a question's gold source URL resolves to an ingested article carrying a captured category
- **THEN** that category is used as the question's true category, with no manual annotation step

#### Scenario: The oracle simulates a classifier, not an answer key

- **WHEN** the oracle boost is applied
- **THEN** it boosts every chunk sharing the gold article's category — not the gold article alone — so the measured lift is an upper bound on a perfect classifier rather than a circular retrieval of the answer

#### Scenario: Weight is swept, not assumed

- **WHEN** the oracle boost is measured
- **THEN** results are reported across a range of `w`, so the ceiling is read off the sweep rather than from one arbitrary weight

#### Scenario: Production retrieval is untouched

- **WHEN** the deployment serves a live query
- **THEN** no category boost is applied, because the oracle exists only in the benchmark harness

### Requirement: Asymmetric interpretation of the ceiling result

The benchmark's reporting SHALL make explicit that a null result and a positive result carry
unequal weight, so a weak positive is not mistaken for a mandate to build. The oracle is an
upper bound measured on a small bank whose gold sources favor the boosted corpus, therefore:

- A **null** result is decisive. If a *perfect* mapping, on a bank whose gold sources favor
  the boosted corpus, produces no lift, then no real classifier can — the feature SHALL be
  dropped.
- A **positive** result is provisional. It establishes only that a ceiling exists; it does
  not license tuning `w` or selecting a classifier until the bank is expanded, because the
  current bank is thin (a handful of distinct gold articles, two of which dominate).

#### Scenario: Perfect mapping yields no lift

- **WHEN** the oracle sweep produces no material improvement over baseline at any `w`
- **THEN** the result is reported as decisive against the feature, and no classifier is built

#### Scenario: Perfect mapping yields lift on a thin bank

- **WHEN** the oracle sweep shows material lift
- **THEN** the result is reported as a provisional ceiling requiring bank expansion before `w` is tuned or a classifier is chosen, rather than as a green light
