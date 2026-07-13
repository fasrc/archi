## ADDED Requirements

### Requirement: Retrieval scoring against gold sources without answer generation

The system SHALL provide a benchmark that scores retrieval directly, constructing **no
answer-generation LLM**. For each question carrying gold sources (the human-verified URL(s)
where the answer lives), it SHALL retrieve the top-k documents via the deployment's
configured retriever and report **hit-rate@k** (was any gold source retrieved) and **MRR**
(reciprocal rank of the first gold source).

Questions with no gold sources (`should_refuse` anchors) SHALL be excluded from hit-rate and
MRR rather than scored as misses, since for those the correct behavior is to retrieve nothing
useful.

Retrieval still embeds the query, so the benchmark MAY require **embedding-provider**
credentials when the deployment is configured with a hosted embedder (e.g.
`OpenAIEmbeddings`). The no-credentials guarantee is therefore scoped to the
answer-generation LLM only, and holds end-to-end only for local-embedding deployments (FASRC
dev uses `HuggingFaceEmbeddings`).

Because it generates no answer, the benchmark SHALL run in-process against a live corpus and
SHALL NOT require a redeploy or a re-ingest to vary a retrieval scoring parameter.

#### Scenario: Gold-sourced question is scored on rank

- **WHEN** a question carrying gold sources is run through the configured retriever
- **THEN** hit-rate@k reflects whether any gold source appears in the top-k, and MRR reflects the rank of the first one

#### Scenario: Refusal anchors are excluded from recall metrics

- **WHEN** a `should_refuse` question (no gold sources, by design) is run
- **THEN** it contributes to neither hit-rate@k nor MRR, and its exclusion is reported rather than silently applied

#### Scenario: No answer-generation credentials required

- **WHEN** the benchmark runs against a local-embedding deployment with no LLM API keys present
- **THEN** it completes and reports its metrics, because no answer is generated

#### Scenario: Hosted embedder still needs its own credential

- **WHEN** the deployment is configured with a hosted embedding provider
- **THEN** the benchmark documents that the embedding credential is still required, rather than claiming a blanket no-key guarantee

#### Scenario: A scoring parameter is swept without redeploying

- **WHEN** the operator sweeps a retrieval scoring weight across several values
- **THEN** every value is measured against the same already-ingested corpus in one run, with no deploy or ingest between values

### Requirement: URL reconciliation before matching or joining

The benchmark SHALL canonicalize URLs on both sides before matching a retrieved document to a
gold source, and before joining a gold source to its captured category. Authored bank URLs and
ingested `documents.url` values are known to differ in form — the bank's own README warns that
SOURCES mode "needs URL reconciliation" because the sitemap-driven SPLIT ingest may store a
slightly different slug, and source-list generation can collapse trailing slashes.

Without this, a retrieved gold article can be scored as a miss and the oracle can silently
resolve to no category — so the sweep would measure **URL-format drift** rather than the
category boost. The benchmark SHALL therefore report any gold source that fails to resolve to
an ingested document, rather than silently treating it as a miss.

#### Scenario: Trailing-slash and slug variants still match

- **WHEN** a bank gold source and the ingested document URL differ only in canonicalizable form (e.g. trailing slash)
- **THEN** they match, and the question is scored as a hit rather than a spurious miss

#### Scenario: Unresolvable gold source is surfaced, not swallowed

- **WHEN** a gold source URL resolves to no ingested document under canonicalization
- **THEN** the run reports it as unresolved, so a broken join is visible instead of masquerading as a retrieval failure

### Requirement: Oracle category-boost sweep measures the ceiling only

The benchmark SHALL support an experiment-only **oracle** boost that applies a category match
using the true category of the question's gold article, so that the upper bound on
category-aware retrieval can be measured *before* any query→category classifier is built:

```
score' = rerank_score + w · (chunk.category == gold_category(q))
```

`gold_category(q)` is derived by joining the question's canonicalized gold source URL to that
article's captured `metadata["category"]` — yielding query→category labels with no
hand-labeling. The oracle boosts every chunk sharing the gold article's *category*, not the
gold article itself, so it simulates a perfect classifier rather than trivially retrieving the
answer.

The oracle SHALL be understood as a **benefit-only** probe and MUST NOT be used to evaluate
the counter-metrics. By construction it only ever boosts toward the correct category, so it
cannot express the harm cases: refusal anchors have no gold source and non-KB gold articles
have no category, so both receive **zero boost at any `w`** and would appear falsely stable.
Harm is evaluated under the simulated-classifier mode below.

The boost weight `w` SHALL be swept rather than fixed, because it is added to a cross-encoder
score whose scale is model-dependent and cannot be chosen a priori.

This capability SHALL exist only in the benchmark and SHALL NOT alter production retrieval.

#### Scenario: Oracle labels are derived without hand-labeling

- **WHEN** a question's canonicalized gold source URL resolves to an ingested article carrying a captured category
- **THEN** that category is used as the question's true category, with no manual annotation step

#### Scenario: The oracle simulates a classifier, not an answer key

- **WHEN** the oracle boost is applied
- **THEN** it boosts every chunk sharing the gold article's category — not the gold article alone — so the measured lift is an upper bound on a perfect classifier rather than a circular retrieval of the answer

#### Scenario: Oracle results are not admissible as harm evidence

- **WHEN** an oracle-mode run reports stable counter-metrics
- **THEN** that stability is reported as vacuous — the boost applied no category to the at-risk rows — and is NOT counted as evidence that the treatment is safe

#### Scenario: Weight is swept, not assumed

- **WHEN** the oracle boost is measured
- **THEN** results are reported across a range of `w`, so the ceiling is read off the sweep rather than from one arbitrary weight

#### Scenario: Production retrieval is untouched

- **WHEN** the deployment serves a live query
- **THEN** no category boost is applied, because the oracle exists only in the benchmark harness

### Requirement: Simulated-classifier mode to expose the harm channels

The benchmark SHALL support a **simulated-classifier** treatment mode in which the boost
category comes from an authored per-question `assumed_category` — the KB category a plausible
real classifier would assign to that query — rather than from the gold article. Every question
SHALL carry an `assumed_category`, **including** `should_refuse` anchors and questions whose
gold source is non-KB.

This mode exists because the harms are only reachable when an at-risk query is assigned an
in-KB category:

- A `should_refuse` anchor such as *"GPU partition layout on MIT's Engaging cluster"* reads as
  a cluster-usage question, so a real classifier would assign `Cluster Usage` and the boost
  would promote confident FASRC context for a query the assistant must decline.
- A Slurm-answered query would likewise be assigned an in-KB category, boosting KB chunks above
  the SchedMD document that actually answers it.

Neither harm is expressible under the oracle. The counter-metrics SHALL be evaluated in this
mode.

#### Scenario: Refusal anchor is assigned a plausible in-KB category

- **WHEN** a `should_refuse` anchor is run in simulated-classifier mode
- **THEN** its authored `assumed_category` is applied to the boost, so any rise in retrieval confidence for an out-of-scope question is measurable rather than structurally invisible

#### Scenario: Non-KB question is assigned a plausible in-KB category

- **WHEN** a question whose gold source is a non-KB document is run in simulated-classifier mode
- **THEN** its authored `assumed_category` boosts KB chunks, so demotion of the non-KB gold source below the top-k is measurable

#### Scenario: Every question carries an assumed category

- **WHEN** the bank is loaded for a simulated-classifier run
- **THEN** any question lacking an `assumed_category` is reported, because a missing label silently exempts that row from the harm measurement

### Requirement: Counter-metrics reported with every run

The benchmark SHALL report two counter-metrics alongside hit-rate@k and MRR on every run, and
a treatment result SHALL be considered inadmissible if reported without them. This is because
a retrieval treatment can improve headline recall while silently harming a subset of the corpus
or a class of question.

- **`non_kb_share@k`** — the fraction of returned top-k documents that are not FASRC KB pages.
  Only KB pages carry an Echo-KB breadcrumb, so non-KB documents can never receive a
  category-matched boost; a boost a document can never receive is arithmetically a penalty on
  that document.
- **`refusal_confidence`** — the top-k retrieval scores returned for `should_refuse` anchors.
  A category treatment may promote confident in-corpus context for a question the assistant
  should decline, converting a refusal into a confident fabrication.

Both SHALL be read from a **simulated-classifier** run. A stable counter-metric from an
oracle-only run SHALL NOT be reported as a safety result.

#### Scenario: Non-KB documents are displaced by a treatment

- **WHEN** a simulated-classifier run raises hit-rate@k but `non_kb_share@k` falls materially against baseline
- **THEN** the run is reported as a suspected non-KB demotion artifact rather than as a clean improvement

#### Scenario: A treatment inflates confidence on out-of-scope questions

- **WHEN** a simulated-classifier run raises the retrieval scores returned for `should_refuse` anchors against baseline
- **THEN** the run is reported as degrading refusal, because higher-scoring in-corpus context makes a fabricated answer more likely

#### Scenario: Counter-metrics accompany the headline number

- **WHEN** any benchmark run completes
- **THEN** `non_kb_share@k` and `refusal_confidence` are emitted together with hit-rate@k and MRR in the same result record

### Requirement: Asymmetric interpretation of the ceiling result

The benchmark's reporting SHALL make explicit that a null result and a positive result carry
unequal weight, so a weak positive is not mistaken for a mandate to build. The oracle is an
upper bound measured on a small bank whose gold sources favor the boosted corpus, therefore:

- A **null** result is decisive. If a *perfect* mapping produces no lift, no real classifier
  can — the feature SHALL be dropped.
- A **positive** result is provisional. It establishes only that a ceiling exists; it does not
  license tuning `w` or selecting a classifier until the bank is expanded, because the current
  bank is thin (a handful of distinct gold articles, two of which dominate).
- A positive ceiling SHALL NOT be acted on until the simulated-classifier run shows the harm
  channels are clean, since benefit and harm are measured in different modes.

#### Scenario: Perfect mapping yields no lift

- **WHEN** the oracle sweep produces no material improvement over baseline at any `w`
- **THEN** the result is reported as decisive against the feature, and no classifier is built

#### Scenario: Perfect mapping yields lift on a thin bank

- **WHEN** the oracle sweep shows material lift
- **THEN** the result is reported as a provisional ceiling requiring bank expansion and a clean simulated-classifier run before `w` is tuned or a classifier is chosen, rather than as a green light
