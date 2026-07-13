## ADDED Requirements

### Requirement: Retrieval scoring against gold sources without answer generation

The system SHALL provide a benchmark that scores retrieval directly, constructing **no
answer-generation LLM**, and SHALL report its metrics in an explicit hierarchy of evidential
strength rather than as a flat list.

For each question carrying gold sources (the human-verified URL(s) where the answer lives), it
SHALL retrieve the top-k documents via the deployment's configured retriever and report:

- **Primary — structural.** The minimum-weight-to-flip statistics (see "Minimum-weight-to-flip
  is the primary readout"). These are exact properties of the captured candidate pool, not
  sample estimates, and they have no ceiling.
- **Secondary — inferential.** **MRR** (reciprocal rank of the first gold source). It is
  continuous and is therefore the strongest inferential metric a small clustered bank can carry.
- **Guardrail only.** **hit-rate@k** (was any gold source retrieved). hit-rate@k is the coarsest
  available estimator and is saturated by design on this bank: 10 of the 18 gold rows are
  `easy_retrieve` anchors, which the project's own documentation defines as ceiling-pinned
  ("should always score high; if it regresses, the retrieval pipeline broke"). A hit-rate@k
  delta SHALL NOT be used as the primary evidence for or against the treatment; it is a
  regression guardrail.

`recall@k` SHALL NOT be reported as a finer-resolution alternative to hit-rate@k: no row in the
bank carries more than one gold source, so on this bank `recall@k` is definitionally identical
to `hit-rate@k`.

Questions with no gold sources (`should_refuse` anchors) SHALL be excluded from hit-rate and
MRR rather than scored as misses, since for those the correct behavior is to retrieve nothing
useful. They are scored by the harm channels instead, and their exclusion SHALL be reported
rather than silently applied.

Retrieval still embeds the query, so the benchmark MAY require **embedding-provider**
credentials when the deployment is configured with a hosted embedder (e.g.
`OpenAIEmbeddings`). The no-credentials guarantee is therefore scoped to the
answer-generation LLM only, and holds end-to-end only for local-embedding deployments (FASRC
dev uses `HuggingFaceEmbeddings`).

Because it generates no answer, the benchmark SHALL run in-process against a live corpus and
SHALL NOT require a redeploy or a re-ingest to vary a retrieval scoring parameter.

#### Scenario: Metrics are labelled by evidential class

- **WHEN** any run completes
- **THEN** the result record labels each metric as structural, inferential, or guardrail, so a hit-rate@k delta cannot be quoted as the headline result

#### Scenario: A saturated guardrail is reported as uninformative, not as a null

- **WHEN** baseline hit-rate@k sits at or near its ceiling on the gold rows
- **THEN** the run reports hit-rate@k as having no headroom and therefore carrying no information about the treatment, rather than reporting "no lift"

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

### Requirement: Single-retrieval pool capture with offline replay of the ranking tail

The benchmark SHALL capture each query's full candidate pool exactly once and SHALL evaluate
every (boost category, weight) cell by offline replay against that capture, performing no
additional retrieval, embedding, rerank, or database call per cell.

**The capture seam.** The capture SHALL be taken at the production ranking seam — the point at
which the reranked candidate list is produced, after the cross-encoder rerank and before the
parent dedupe (`hierarchical_retriever.py:236`). The retriever SHALL expose exactly one
overridable hook at that point:

```python
def _adjust_ranked_scores(self, query, candidates, ranked):
    """Hook: re-score/re-order `ranked`. Base implementation is identity."""
    return ranked
```

The base implementation SHALL be the identity function, and the introduction of the hook SHALL
leave production ordering **bit-for-bit unchanged**; a test SHALL assert that the base
implementation returns its input list unchanged and that the retriever's output ordering is
unaffected by the hook's existence.

The benchmark SHALL subclass the retriever and override this hook **to record**, not to boost:
the override captures `candidates`, `ranked`, and the rerank scores, and returns `ranked`
unmodified. The boost itself is **never** applied inside the hook, because every (category,
weight) cell is computed by offline replay against the recording. This keeps exactly one live
retrieval path and guarantees the recorded pool is the pool production ranks.

The hook is also the seam a production soft boost would later occupy, so a capture taken through
it validates the production seam rather than a parallel one. It SHALL NOT be added anywhere else,
and no other production code path SHALL be modified by this change.

**Why the replay is exact.** Everything upstream of the boost seam is invariant to the boost.
`_generate_candidates` takes only the query string plus static retriever configuration and
passes no metadata filter (`hierarchical_retriever.py:125-138`). `_rerank` hands FlashRank
passages of the form `{"id", "text", "meta": {}}` — it blanks the metadata channel, so the
rerank score is a pure function of the (query, passage) token pair and cannot see `category`
(`hierarchical_retriever.py:213-218`). The single database call downstream of the seam,
`_fetch_parents`, is a set-membership lookup (`WHERE p.id = ANY(%s)`) returning a dict, and the
id list handed to it is built by an exhaustive loop over the entire pool with no early break
(`hierarchical_retriever.py:243-257`; truncation happens later, at `:271-272`). A different
boost therefore permutes that id list but cannot change its membership, so pre-fetching parents
for the whole pool once is not an approximation — it yields the identical mapping production
would build at any (category, weight).

The capture SHALL record, per candidate: `rerank_score`, `category` (absent for non-KB chunks),
`parent_id`, and `url`; the baseline ranked order as returned by the reranker; the parent record
for every distinct `parent_id` in the pool; and the set of parent ids that fail to resolve.

The replay SHALL reproduce the production tail exactly: stable descending sort with ties broken
by the captured baseline order, first-seen-parent dedupe, parent materialization that skips
unresolved parents **without** charging them against the top-k budget, and truncation at
`num_documents_to_retrieve`.

Captured candidates SHALL be stored as plain immutable records and never as live `Document`
objects, because production mutates `doc.metadata["rerank_score"]` in place
(`hierarchical_retriever.py:269`), so a shared object would leak one grid cell's score into the
next.

The capture cache key SHALL include the query text, `candidate_pool_size`, `semantic_weight`,
`bm25_weight`, the reranker model id, and a corpus snapshot identifier. A change to any of them
SHALL force re-retrieval.

#### Scenario: The base hook is the identity function

- **WHEN** the deployment serves a live query and no benchmark subclass is installed
- **THEN** `_adjust_ranked_scores` returns the `ranked` list it was given, unchanged, and the retriever's output ordering is bit-for-bit identical to its ordering before the hook existed

#### Scenario: The capture is taken through the production seam, and records rather than boosts

- **WHEN** the benchmark captures a candidate pool
- **THEN** it does so by overriding `_adjust_ranked_scores` to record `candidates`, `ranked`, and the rerank scores and to return `ranked` unmodified, so no boost is ever applied inside a live retrieval and the recorded pool is exactly the pool production ranks

#### Scenario: Replay at zero weight reproduces production exactly

- **WHEN** the offline replay is run at `w = 0`
- **THEN** its top-k is identical to the live retriever's top-k for the same query, and the run fails loudly if it is not

#### Scenario: A full category-by-weight grid costs one retrieval per query

- **WHEN** the harness evaluates every in-KB category against every swept weight
- **THEN** it performs exactly one retrieval, one rerank, and one parent fetch per query, and every grid cell is computed by in-memory replay over a pool of at most `candidate_pool_size` elements

#### Scenario: Grid cells cannot contaminate each other

- **WHEN** two grid cells are evaluated in sequence for the same query
- **THEN** the second cell sees the captured baseline scores, not scores mutated in place by the first

#### Scenario: A retrieval-config change invalidates the capture

- **WHEN** `candidate_pool_size`, a hybrid weight, the reranker model, or the corpus snapshot changes
- **THEN** the cached pools are rejected and retrieval is re-run, rather than a stale pool being swept

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

### Requirement: Corpus category census precedes the sweep and may block it

The benchmark SHALL take a census of the ingested corpus before any sweep, SHALL derive the
category vocabulary it sweeps over from that census rather than from any assumed label list, and
SHALL block the sweep when category coverage falls below the pre-registered numeric floor stated
in this requirement.

The census is a **census of the corpus, not a sample of it**, so its findings are bank-independent
and carry no sampling error. It is therefore the one finding in this change that may halt the work
at any bank size (see "Decision authority is bounded by the class of evidence").

The census SHALL report:

- the fraction of KB chunks carrying a non-empty `metadata["category"]`;
- the **observed category vocabulary** — the set of distinct non-empty `metadata["category"]`
  values on KB chunks — and its cardinality;
- the distribution of KB chunks over that vocabulary.

The vocabulary SHALL be **measured**, not assumed. It SHALL NOT be taken from the proposal's
claimed 19-label site taxonomy, and it SHALL NOT be taken from the 6-label list at
`deploy/fasrc-dev/config.yaml` — that list is the `llm_category` field, a different attribute the
boost does not read. Every category-indexed sweep in this capability ranges over the measured
vocabulary.

**The coverage floor is pre-registered as a number, before the census runs.** Let `coverage_KB`
be the fraction of KB chunks carrying a non-empty `metadata["category"]`, and `|V|` the
cardinality of the measured vocabulary. The following thresholds SHALL be fixed before the census
is taken and SHALL NOT be adjusted after seeing its result:

- **`coverage_KB < 0.50`, or `|V| < 2` ⇒ `CORPUS_BLOCKED`; the sweep SHALL NOT run.** Below 50%
  the majority of the KB corpus is, with respect to the boost predicate, indistinguishable from
  the non-KB corpus: an uncategorized KB chunk can never be boosted, exactly like a SchedMD page.
  The "category boost" would then be mostly a *labelled-vs-unlabelled* discriminator rather than a
  *category* discriminator — a different feature, one that penalizes every uncategorized KB
  article the way H1 penalizes non-KB documents. A null there would measure the **breadcrumb
  extractor**, not the idea. With `|V| < 2` the predicate is constant and the boost is a global
  additive offset, which reorders nothing at any weight.
- **`0.50 <= coverage_KB < 0.80` ⇒ the sweep MAY run but SHALL be explicitly scoped.** Every gold
  row whose article carries no category SHALL be excluded from the benefit side and reported as
  excluded, the uncategorized KB chunk fraction SHALL be reported, and every result SHALL be
  labelled "scoped to the covered subset (coverage = X%)". `CORPUS_BLOCKED` is not recorded, but
  no result may be quoted without that scope label.
- **`coverage_KB >= 0.80` ⇒ unscoped run.**

A `CORPUS_BLOCKED` finding SHALL be reported as a defect in extraction, and extraction SHALL be
fixed or the experiment explicitly scoped to the covered subset before proceeding. Because the
thresholds are fixed in advance, no post-hoc judgment about whether coverage is "low enough"
enters the gate.

#### Scenario: The category vocabulary is measured, not assumed

- **WHEN** the sweep is configured
- **THEN** its category axis is the set of distinct non-empty `metadata["category"]` values observed on KB chunks, with its cardinality reported, rather than a label list taken from the proposal or from the unrelated `llm_category` config

#### Scenario: Category coverage below the pre-registered floor blocks the sweep

- **WHEN** fewer than 50% of KB chunks carry a captured category, or the measured vocabulary has fewer than 2 distinct labels
- **THEN** the sweep does not run, the verdict is `CORPUS_BLOCKED`, and the finding is recorded as a defect in the extractor rather than as a verdict on the boost

#### Scenario: Partial coverage runs, but every number carries its scope

- **WHEN** category coverage lands between 50% and 80%
- **THEN** the sweep runs over the covered subset only, the excluded gold rows and the uncovered chunk fraction are reported, and every result is labelled with the coverage it was scoped to

#### Scenario: The floor is fixed before the census is read

- **WHEN** the census result is known
- **THEN** the coverage thresholds are the ones pre-registered in this requirement, and an operator judgment that the observed coverage is "good enough" or "too low" carries no weight against them

#### Scenario: The census can halt the work at any bank size

- **WHEN** the census shows the corpus cannot support the boost predicate
- **THEN** that finding may block the feature even though the bank is underpowered, because a census of the corpus is not subject to the bank's sampling limits

### Requirement: Headroom precheck bounds the inferential metrics before the sweep

The benchmark SHALL compute and report, from a single baseline run and before any weight is
swept, the maximum lift the treatment could possibly produce on each **inferential** metric, and
SHALL declare that metric **instrument-dead** when its maximum is zero. It SHALL NOT declare the
primary structural readout instrument-dead, and it SHALL NOT suppress the harm sweep.

The boost reorders only *inside* the candidate pool returned by `hybrid_search`
(`hierarchical_retriever.py:125-138`); it cannot recall a document the pool never contained.
The headroom is therefore exact, not an estimate.

`max_possible_lift` is defined for the **inferential** metrics only — the two metrics that have a
ceiling. The primary structural readout (minimum-weight-to-flip) has **no** ceiling and SHALL NOT
be described as instrument-dead by this precheck:

```
max_possible_lift(hit@k) = pool_recall@candidate_pool_size − baseline_hit@k
max_possible_lift(MRR)   = MRR_pool_ceiling − baseline_MRR
```

where `MRR_pool_ceiling` assigns reciprocal rank 1 to every row whose gold source appears anywhere
in the captured pool and 0 to every row whose gold source does not.

`max_possible_lift(MRR)` is the **strictly stronger** of the two and is therefore the single
computable trigger: it is zero exactly when every gold source that is in the pool at all is
*already at rank 1 of the returned top-k*. That condition entails `max_possible_lift(hit@k) = 0`
(rank 1 implies inside the top-k), and it further entails `W_benefit = +∞` — no weight, however
large, can promote a gold source that is already first or recall one the pool never fetched. So
when MRR headroom is zero, the benefit side of the **primary** readout is dead too, and the run
records the verdict `NO_HEADROOM`.

The precheck SHALL report `pool_recall@candidate_pool_size`, `baseline_hit@k`, `baseline_hit@1`,
`baseline_MRR`, `MRR_pool_ceiling`, and the full distribution of the rank of the first gold source
within the pool (including "not in pool at all"), so that MRR saturation is **measured** rather
than assumed.

When `max_possible_lift` is zero for a metric, a null delta on that metric SHALL NOT be reported
as evidence about the feature — it is an artifact of the instrument.

A `NO_HEADROOM` finding SHALL NOT stop the harm sweep. `w*_harm`, `W_safe`, and the full
(row × category × weight) harm matrix remain computable and remain the only thing in this change
that can halt the feature, so the sweep SHALL still run and the harm gate SHALL still be applied.
What `NO_HEADROOM` stops is the *interpretation of a benefit null*, not the run.

#### Scenario: Gold is always already inside the top-k

- **WHEN** `pool_recall@candidate_pool_size` equals `baseline_hit@k`
- **THEN** hit-rate@k is reported as instrument-dead, the sweep's null on it is reported as an artifact of the instrument, and no kill decision is drawn from it

#### Scenario: Every in-pool gold source already ranks first

- **WHEN** `max_possible_lift(MRR)` is zero
- **THEN** the run records `NO_HEADROOM`, states that `max_possible_lift(hit@k)` is necessarily zero as well and that `W_benefit` is `+∞`, and still runs the harm sweep, because the benefit side being dead does not make the harm side unobservable

#### Scenario: Headroom exists and is quantified before the sweep

- **WHEN** some gold sources sit inside the pool but below the top-k cut
- **THEN** the run reports the exact number of rows any weight could in principle flip, and that number is carried as the hard ceiling on any lift the sweep may report

#### Scenario: The structural readout is never declared instrument-dead

- **WHEN** the headroom precheck reports zero possible lift on hit-rate@k
- **THEN** `w*_gold_hit`, `w*_gold_rank`, `w*_harm`, `W_safe`, and the operating window are still computed and reported, because `max_possible_lift` is a property of the two ceilinged inferential metrics and not of the primary structural readout

#### Scenario: The rank distribution is published, not summarized away

- **WHEN** the precheck completes
- **THEN** the rank-of-first-gold distribution is emitted in the result record, so saturation of MRR is visible rather than assumed away

### Requirement: Boost-degeneracy precheck over the candidate pool

The benchmark SHALL report, per query and in aggregate, the fraction of pool candidates carrying
the treatment category, and SHALL declare the boost **structurally inert** for a query when no
candidate pair in that query's pool can be reordered by any weight.

A uniform additive offset is order-preserving. `hybrid_search` already selects topically relevant
chunks, so a pool dominated by the query's own category receives the boost almost everywhere and
the transform degenerates toward `score + w`, which cannot reorder anything. A null produced this
way says nothing about sample size and nothing about the idea — it says the mechanism has no
purchase on the pool.

The precheck SHALL report, per query: the pool category-match fraction, and the number of ordered
pairs `(i, j)` in which `i` carries the treatment category, `j` does not, and `j` currently
outranks `i`. That pair set is the complete set of reorderings any weight could ever achieve. A
query with an empty pair set SHALL be reported as structurally inert, and inert rows SHALL be
counted and reported separately from rows on which the mechanism can act.

Because `category` is written identically onto a parent and every one of its children at ingest
(`manager.py:835-843`), it is an **article-level** attribute: the boost adds the same constant to
every chunk of an article and can therefore only reorder *across* articles, never within one.
Inertness SHALL be assessed across articles accordingly.

#### Scenario: The pool is category-homogeneous

- **WHEN** substantially all pool candidates for a query carry the treatment category
- **THEN** the query is reported as structurally inert, because the boost is a near-uniform offset that cannot change the returned order at any weight

#### Scenario: Inert rows are not silently averaged into the delta

- **WHEN** an aggregate delta is reported
- **THEN** the count of structurally inert rows is reported beside it, so a delta diluted toward zero by inert rows is not misread as a measured absence of effect

### Requirement: The swept weight range is bounded below the lexicographic regime

The benchmark SHALL sweep `w` only over a bounded, non-degenerate operating range derived from the
captured pools, and SHALL exclude from every gate, window, and verdict any weight at which the
boost has stopped being a soft re-scoring and become a hard category preference.

An unbounded sweep makes the harm gate fire vacuously. Rerank scores are bounded, so for a large
enough `w` the additive boost is **lexicographic**: every category-matched candidate outranks every
unmatched one regardless of what the cross-encoder said. At that point a harm event is an
arithmetic certainty for essentially any pool, and "some (row, category, weight) cell shows harm"
becomes a statement about arithmetic rather than about the feature — the mirror image of the
"decisive null" this change withdrew.

The bound SHALL be **derived, not chosen**:

- `w_lex(q)` = (maximum rerank score in `q`'s captured pool) − (minimum rerank score in `q`'s
  captured pool). For any `w >= w_lex(q)` the boost is lexicographic on `q`.
- **`W_LEX` = the minimum of `w_lex(q)` over the bank's queries** — the smallest weight at which
  the boost has gone lexicographic on at least one bank query.
- **The admissible operating range is `0 < w < W_LEX`.**

Weights at or above `W_LEX` SHALL be reported as the **degenerate regime** and SHALL NOT contribute
to `W_safe`, `W_benefit`, the operating window, or any verdict. A hard category preference is a
different feature with different (and strictly worse) harm properties, and a hard metadata filter
is an explicit non-goal of this change.

`W_LEX`, the per-query `w_lex(q)`, and the observed rerank-score range SHALL be reported with every
run, because the operating range is a property of the reranker's score scale and will move if the
reranker model changes.

Behavior in the degenerate regime MAY be reported separately as a diagnostic — labelled
"hard-filter equivalent, out of scope" — but SHALL NOT be quoted as a result about the soft boost.

#### Scenario: The sweep stops below the lexicographic weight

- **WHEN** the breakpoint set of `w` is enumerated from the captured pools
- **THEN** only breakpoints strictly below `W_LEX` are swept, and weights at or above it are reported as the degenerate regime rather than swept as if they were candidate operating points

#### Scenario: A harm event in the degenerate regime does not fire the gate

- **WHEN** the only weights at which any harm event occurs are at or above `W_LEX`
- **THEN** `W_safe` is reported as `+∞` within the operating range, the degenerate-regime harm is reported as an arithmetic property of a hard category preference, and it does not halt the no-classifier path

#### Scenario: The operating range is reported, not assumed

- **WHEN** any weight-indexed result is reported
- **THEN** it carries `W_LEX`, the observed rerank-score range, and the reranker model id, so a reader can see that the range is derived from this reranker's score scale rather than chosen

### Requirement: Minimum-weight-to-flip is the primary readout

The benchmark SHALL report, as its primary result, the distribution of the minimum boost weight
required to change the returned top-k, computed exactly from the captured candidate pool rather
than by sampling a grid of weights, and SHALL derive from it the operating window in which the
treatment could be used at all.

Because the boost is additive and monotone (`score' = rerank_score + w · 1[chunk.category == c]`),
a matching candidate `i` overtakes a non-matching candidate `j` exactly when `w > s_j − s_i`. The
returned top-k is therefore a **step function of `w` whose breakpoints are precisely the pairwise
rerank-score gaps in the pool**. The complete sweep is arithmetic over the capture, at unlimited
weight resolution and with no re-retrieval.

All quantities below are computed **within the admissible operating range `0 < w < W_LEX`** (see
"The swept weight range is bounded below the lexicographic regime"). Every one of them takes the
value `+∞` when the set it minimizes over is **empty** or when no admissible weight achieves it;
the empty-set convention is stated explicitly for each, because a silent `min` over an empty set is
how a vacuous verdict gets manufactured.

The following SHALL be reported:

- **`w*_gold_hit(r)`** — the smallest admissible `w` that promotes row `r`'s gold source **into**
  the top-k under the **oracle** category. Defined only for KB gold rows that baseline **misses**.
  It is `+∞` where the gold source is not in the candidate pool at all (no weight can recall a
  document `hybrid_search` never fetched) and `+∞` where the promotion requires a weight at or
  above `W_LEX`.
- **`w*_gold_rank(r)`** — the smallest admissible `w` that **strictly improves the rank** of row
  `r`'s gold source within the returned top-k under the **oracle** category. Defined only for KB
  gold rows that baseline **hits** at a rank below 1. It is `+∞` for a row whose gold source
  already ranks 1, and `+∞` where no admissible weight improves the rank. Rank improvement is a
  real benefit — it is what MRR measures, and it is the **only** benefit a hit-saturated bank can
  express — so it SHALL NOT be collapsed to zero.
- **`already_hit(r)`**, **`already_first(r)`** — flags recording that baseline retrieval already
  returns the gold source, and already returns it first. These are reported as flags, never as a
  `w*` of zero, because a zero would enter a minimum and destroy it.
- **`w*_harm(r)`** — the smallest admissible `w` at which row `r` suffers **any** harm event (H1,
  H2, or H3; see "Harm is gated on an adversarial worst-case category sweep") under **any**
  category in the measured vocabulary. `+∞` if no admissible weight harms `r`.
- **`W_safe`** = the minimum of `w*_harm(r)` over all instrumented rows; **`+∞` if no row is ever
  harmed at an admissible weight**.
- **`W_benefit_hit`** = the minimum of `w*_gold_hit(r)` over the KB gold rows that baseline
  **misses**; **`+∞` when that set is empty** (a fully hit-saturated bank) or when no member is
  reachable at an admissible weight.
- **`W_benefit_rank`** = the minimum of `w*_gold_rank(r)` over the KB gold rows that baseline
  **hits below rank 1**; **`+∞` when that set is empty** (every gold source already first) or when
  no member is reachable at an admissible weight.
- **`W_benefit`** = `min(W_benefit_hit, W_benefit_rank)` — the first admissible weight at which the
  treatment does anything good for any gold row in the bank. `+∞` when both components are.

**The domination test is conditioned on benefit being reachable at all.** It SHALL be evaluated
only when `W_benefit` is **finite**:

- **`W_benefit` finite and `W_safe <= W_benefit`** ⇒ the benchmark SHALL report that **no
  admissible weight helps any question in this bank before worst-case harm becomes reachable** —
  the feature is **dominated** on the rows measured.
- **`W_benefit` finite and `W_safe > W_benefit`** ⇒ an **operating window** `[W_benefit, W_safe)`
  exists (intersected with the admissible range).
- **`W_benefit = +∞`** ⇒ the run SHALL **NOT** print the domination conclusion. `min` over an empty
  or unreachable benefit set is not evidence that harm precedes benefit; it is evidence that this
  bank has no benefit to precede. The finding is reported instead as one of:
  - **`NO_HEADROOM`** — no weight of any size could help, because every in-pool gold source is
    already at rank 1 (equivalently, `max_possible_lift(MRR) = 0`); or
  - **benefit reachable only in the degenerate regime** — some weight could help, but only at or
    above `W_LEX`, i.e. only once the boost has become a hard category preference. This SHALL be
    reported as a finding about the *soft* boost having no purchase on this bank, and SHALL NOT be
    read as licensing a hard filter, which is out of scope.

These are per-row entailments over captured scores, not population estimates. They SHALL be
reported **without** confidence intervals, and the record SHALL state that they characterize the
rows measured and do not generalize to unmeasured rows. The record SHALL further state that
`W_safe` is a **minimum over instrumented rows**, so adding at-risk rows can only lower it:
`W_safe` is an **optimistic** ceiling, never a conservative one.

This readout SHALL remain the primary result even when baseline hit-rate@k is 1.0, which is
precisely the regime in which hit-rate@k is blind and `W_benefit_rank` is the only live benefit
channel.

#### Scenario: Harm arrives before benefit

- **WHEN** `W_benefit` is finite and the harm-free ceiling `W_safe` is at or below it
- **THEN** the run reports that every admissible weight capable of helping any bank question already admits a worst-case harm event, and records this as an entailment of the captured pools rather than as a statistical result

#### Scenario: An operating window exists

- **WHEN** `W_benefit` is finite and `W_safe` strictly exceeds it
- **THEN** the window `[W_benefit, W_safe)` is reported as the interval of weights in which benefit is reachable and no worst-case harm event fires, and it licenses only bank expansion — not weight tuning and not classifier selection

#### Scenario: A hit-saturated bank still yields a benefit onset

- **WHEN** every KB gold row is a baseline hit, so the baseline-miss set is empty
- **THEN** `W_benefit_hit` is reported as `+∞` by the stated empty-set convention, `W_benefit_rank` supplies the benefit onset from rank improvements, and `W_benefit` is their minimum

#### Scenario: An empty benefit set does not manufacture a domination verdict

- **WHEN** `W_benefit` is `+∞` because no admissible weight helps any gold row
- **THEN** the run does NOT report the feature as dominated, and instead reports either `NO_HEADROOM` or "benefit reachable only in the degenerate regime", because a minimum over an empty set is not a finding about harm preceding benefit

#### Scenario: The safe ceiling is reported as optimistic

- **WHEN** `W_safe` is reported
- **THEN** it is accompanied by the statement that it is a minimum over the instrumented rows, so adding at-risk rows can only lower it and it must not be read as a conservative bound

#### Scenario: The gold source is outside the candidate pool

- **WHEN** a row's gold source appears nowhere in the captured candidate pool
- **THEN** its `w*_gold_hit` is reported as `+∞`, because no weight can promote a document the hybrid stage never fetched

#### Scenario: An already-hit row is flagged, not zeroed

- **WHEN** baseline retrieval already returns a row's gold source
- **THEN** the row carries an `already_hit` flag and its rank-improvement onset `w*_gold_rank`, and it does not contribute a `w*` of zero to any minimum

#### Scenario: The sweep is exact, not sampled

- **WHEN** the weight sweep is reported
- **THEN** it is derived from the pairwise rerank-score gaps in the captured pool, restricted to the admissible operating range, so no behavior between two sampled weights can be missed

#### Scenario: A saturated bank still yields a reading

- **WHEN** baseline hit-rate@k is 1.0 and every gold source already ranks first
- **THEN** `w*_harm`, `W_safe`, and the harm matrix are still reported, `W_benefit` is `+∞` with `NO_HEADROOM` recorded, and the run is not recorded as "no effect"

### Requirement: Oracle sweep is one column of the category matrix and bounds benefit only

The benchmark SHALL support an experiment-only **oracle** treatment that boosts using the true
category of the question's gold article, so that an upper bound on category-aware retrieval can
be measured *before* any query→category classifier is built:

```
score' = rerank_score + w · (chunk.category == gold_category(q))
```

`gold_category(q)` is derived by joining the question's canonicalized gold source URL to that
article's captured `metadata["category"]` — yielding query→category labels with no hand-labeling.
The oracle boosts every chunk sharing the gold article's *category*, not the gold article itself,
so it simulates a perfect classifier rather than trivially retrieving the answer.

The oracle SHALL be implemented as a **selection from the same (category × weight) matrix** the
pool capture already makes available — the column in which each row's boost category is its gold
category. It is not a separate retrieval pass and SHALL NOT be costed as one.

The oracle SHALL be understood as a **benefit-only** probe and MUST NOT be used to evaluate the
harm channels. By construction it only ever boosts toward the correct category, so it cannot
express the harm cases: refusal anchors have no gold source, non-KB gold articles carry no
`category` key at all, and a correctly-routed KB row is by definition not misrouted — so all
three at-risk populations would appear falsely stable.

The boost weight `w` SHALL be swept rather than fixed, because it is added to a cross-encoder
score whose scale is model-dependent and cannot be reasoned about a priori. The sweep SHALL be
confined to the admissible operating range `0 < w < W_LEX`.

The authority of an oracle result — in either direction — SHALL be governed by the requirement
"Decision authority is bounded by the class of evidence". A ceiling number is an estimate, and it
carries no more weight than the bank's cluster count allows.

This capability SHALL exist only in the benchmark and SHALL NOT alter production retrieval.

#### Scenario: Oracle labels are derived without hand-labeling

- **WHEN** a question's canonicalized gold source URL resolves to an ingested article carrying a captured category
- **THEN** that category is used as the question's true category, with no manual annotation step

#### Scenario: The oracle simulates a classifier, not an answer key

- **WHEN** the oracle boost is applied
- **THEN** it boosts every chunk sharing the gold article's category — not the gold article alone — so the measured lift is an upper bound on a perfect classifier rather than a circular retrieval of the answer

#### Scenario: Oracle results are not admissible as harm evidence

- **WHEN** an oracle-mode run reports stable harm metrics
- **THEN** that stability is reported as vacuous — the boost never misroutes and never touches the at-risk populations — and is NOT counted as evidence that the treatment is safe

#### Scenario: The oracle is one cell of the shared grid

- **WHEN** the oracle arm is evaluated
- **THEN** it reuses the captured candidate pool rather than issuing a fresh retrieval, because the boosted category is the only thing that differs from the harm arms

#### Scenario: Weight is swept, not assumed

- **WHEN** the oracle boost is measured
- **THEN** results are reported across the full breakpoint set of `w` inside the admissible operating range, so the ceiling is read off the sweep rather than from one arbitrary weight, and no reading is taken from the degenerate regime at or above `W_LEX`

#### Scenario: Production retrieval is untouched

- **WHEN** the deployment serves a live query
- **THEN** no category boost is applied, because the oracle exists only in the benchmark harness

### Requirement: Harm is gated on an adversarial worst-case category sweep

The benchmark SHALL evaluate harm by sweeping **every category in the measured vocabulary**
against **every** question at every swept weight, and SHALL gate on the **worst** cell; it SHALL
NOT accept any authored, hand-picked, or otherwise single-label category as evidence of safety.

Three binary harm events SHALL be computed per (row `r`, category `c`, weight `w`) from the
captured pool:

- **H1 — non-KB displacement.** For a row whose gold source is non-KB, the gold source is present
  in the baseline top-k but absent from the boosted top-k. Non-KB documents carry no `category`
  key at all, so they take a `+0` boost at every grid point and are strictly demoted relative to
  any boosted KB chunk.
- **H2 — refusal context injection.** For a `should_refuse` anchor, the boosted top-k contains at
  least one document that was **not** in the baseline top-k and that is a KB page carrying
  category `c` — the boost has newly manufactured category-matched FASRC context for a question
  the assistant must decline.
- **H3 — in-KB misrouting.** For a KB gold row that is a baseline hit, the gold source falls out
  of the boosted top-k under some category `c != gold_category(q)`. This is what an ordinary
  classifier error does in production, it is the most likely harm in practice, and it makes
  **every** gold row an at-risk row rather than only the refusal and non-KB rows.

A row is **harmed at `w`** iff some category in the measured vocabulary triggers H1, H2, or H3
at `w`. The harm sweep therefore ranges over **every row in the bank**, not only the refusal and
non-KB rows. Harm events SHALL be evaluated only at **admissible** weights (`0 < w < W_LEX`); a
harm event that fires only in the degenerate regime is an arithmetic property of a hard category
preference and SHALL NOT be counted (see "The swept weight range is bounded below the
lexicographic regime").

**The gate is weight-conditioned, and it has exactly one trigger.** A harm event is not by itself
a halt: harm at a weight *above* the benefit onset merely bounds the operating window from above,
which is the ordinary case in which a usable window exists. The single halt trigger, used
identically everywhere in this capability, is:

> **`W_benefit` is finite and `W_safe <= W_benefit`** — worst-case harm becomes reachable at or
> below the first admissible weight that helps any question. Verdict `HARM_REACHABLE`.

A harm event at `w > W_benefit` SHALL be reported (it is the upper edge of the operating window)
and SHALL NOT halt. A harm event at any weight when `W_benefit = +∞` SHALL be reported, and the
finding recorded is that this bank shows harm reachable and no benefit reachable at all — which
also fires the halt trigger, since `W_safe <= +∞` holds for any finite `W_safe`.

The gate is asymmetric, and the report SHALL say so:

- A **clean** worst case (`W_safe = +∞` over the admissible range) is a safety certificate,
  obtained without a classifier existing and without a human choosing a label. Its strength is
  bounded by the at-risk unit counts (see "A clean harm result is bounded by the at-risk units
  observed") **and by the scope condition below**.
- A **dirty** worst case that fires the halt trigger is **not** proof that the feature harms —
  only that harm is reachable before benefit. It SHALL **halt** the "proceed without a classifier"
  path rather than kill the feature: safety may then be certified only against a real candidate
  classifier's actual output distribution.

**Scope condition on the certificate (binding, and it is not satisfied by any classifier that
exists today).** Worst-case-over-single-categories is an upper bound on harm **only for a boost
that adds `w` to exactly one category per query under a hard-match predicate**
(`chunk.category == c`). It is **not** an upper bound for:

- a **multi-label** boost that boosts more than one category per query, or
- a **soft** boost of the form `w · P(c | q)` over a label distribution,

because there the displaced set is the **union** over the boosted categories, and the union can
strictly exceed the harm of any single category — so the max over single categories is not an
upper bound over category *sets*. Both classifier candidates under consideration
(embedding-affinity centroids; an agent tool argument) naturally emit a distribution rather than a
hard single label, so this is a live risk, not a hypothetical.

The certificate SHALL therefore be recorded with a **binding precondition on the successor change
`decide-category-boost`**: either (a) production boosts **exactly one** category per query with a
hard-match predicate, in which case this certificate transfers; or (b) production uses a
multi-label or soft boost, in which case the certificate **does not transfer**, and the sweep SHALL
be re-run over category **sets** (or directly against the classifier's actual output distribution)
before any safety claim is made. A run SHALL NOT state a safety certificate without stating which
branch it is conditional on.

The report SHALL name the **arg-max category** that produced each row's worst cell, so a reader
can judge whether the damaging routing is one a real classifier would plausibly emit, and SHALL
emit the full **(row × category × weight) harm matrix** — not merely its worst cell — so that a
candidate classifier's label distribution can later be composed with it to obtain an
expected-harm figure with no re-retrieval.

The full matrix is affordable precisely because of the pool-capture requirement: each cell is one
in-memory stable sort over at most `candidate_pool_size` elements, with zero retrieval, embedding,
or database work. **Cost is therefore not an admissible reason to narrow the gate to a single
label.**

#### Scenario: The harm gate ranges over every category, not a chosen one

- **WHEN** the harm channels are evaluated
- **THEN** every category in the measured vocabulary is applied to every row and the gate reads the worst cell, so no human choice of label can determine how much harm the experiment is able to find

#### Scenario: In-KB misrouting is measured, not assumed away

- **WHEN** a KB gold row that baseline retrieves is boosted under a category other than its gold category
- **THEN** any loss of the gold source from the top-k is recorded as an H3 harm event, because that is exactly what an imperfect classifier will do in production

#### Scenario: A refusal anchor is probed under every category

- **WHEN** a `should_refuse` anchor is swept
- **THEN** every category in the measured vocabulary is applied to it in turn, and any newly-injected category-matched KB document is recorded as an H2 harm event

#### Scenario: A non-KB gold row is probed under every category

- **WHEN** a question whose gold source is non-KB is swept
- **THEN** every category is applied in turn and the weight at which the non-KB gold source leaves the top-k is recorded as H1, because a document that can never be boosted is arithmetically penalized by any boost

#### Scenario: A clean worst case certifies safety without a classifier

- **WHEN** no row suffers H1, H2, or H3 under any category at any admissible weight
- **THEN** the result is reported as a sound upper bound on harm for a single-label hard-match boost, subject to the at-risk unit bound and to the stated scope condition

#### Scenario: The certificate names its scope condition

- **WHEN** a clean worst case is written up as a safety certificate
- **THEN** it states that the bound holds only for a boost that adds `w` to exactly one category per query under a hard-match predicate, and records the obligation on `decide-category-boost` to re-run the sweep over category sets if production uses a multi-label or soft `w · P(c | q)` boost

#### Scenario: A soft-boost production form voids the certificate

- **WHEN** the successor change proposes a boost over a classifier's label distribution rather than a single hard label
- **THEN** the clean worst-case certificate from this change does NOT transfer, because the displaced set is the union over the boosted categories and can exceed the harm of any single category, and a fresh sweep over category sets or over the classifier's own output distribution is required

#### Scenario: A dirty worst case at or below the benefit onset halts rather than kills

- **WHEN** `W_safe <= W_benefit` with `W_benefit` finite, or harm is reachable while `W_benefit` is `+∞`
- **THEN** the result is reported as "harm reachable; safety not certifiable without a real classifier's outputs", the arg-max category is named, and the result is NOT reported either as proof the feature harms or as grounds to proceed on an assumed label

#### Scenario: A harm event above the benefit onset bounds the window instead of halting

- **WHEN** the lowest admissible weight at which any harm event fires strictly exceeds `W_benefit`
- **THEN** the harm event is reported as the upper edge of the operating window `[W_benefit, W_safe)` and does NOT halt the no-classifier path, because a harm event is only a halt when it precedes every benefit

#### Scenario: The harm matrix outlives the run

- **WHEN** the worst-case sweep completes
- **THEN** the full (row × category × weight) harm matrix is emitted, so a future classifier's label distribution can be scored against it without re-running retrieval

### Requirement: Authored category labels are informational and MUST NOT gate any result

The benchmark MUST NOT consume an authored per-question category guess — such as an
`assumed_category` bank field — as an input to any pass/fail decision, any safety claim, or any
headline harm number.

An authored label encodes its author's belief about a classifier that does not exist yet. A
charitable label makes `non_kb_share@k` and `refusal_confidence` look stable even when a real
classifier would route the at-risk query somewhere more damaging, so gating harm on it would make
the authors' own judgment the safety oracle. Auditable `notes` make an authored assumption
*visible*; they do not make it *representative*. Because the adversarial worst-case sweep costs
nothing extra, there is no cost justification for an authored label in the gate.

No authored label is required by this capability, and none SHALL be added to the bank for the
purpose of computing a harm metric. An authored guess MAY be recorded and reported **only** as a
**non-normative annotation** — a point plotted on the worst-case harm surface, so a reader can see
whether the guess is more charitable than the worst case. A missing annotation SHALL NOT block a
run and SHALL NOT change any number.

A **representative** (rather than worst-case) harm figure SHALL come only from an actual candidate
classifier's output on the query, reported as a distribution-weighted figure **in addition to**
the worst case. It does not replace the worst case, and the gate still fails if the worst case
fails.

#### Scenario: A missing authored label does not weaken the harm result

- **WHEN** a question carries no authored category guess
- **THEN** the run completes and the harm result for that question is unchanged, because harm is computed over the whole measured vocabulary

#### Scenario: A charitable authored label cannot clear the gate

- **WHEN** an authored label for an at-risk row shows no harm but some other category in the measured vocabulary triggers a harm event
- **THEN** the gate fails on the worst-case category, and the authored label is reported only as an annotation on the harm surface

#### Scenario: An authored label is never a safety claim

- **WHEN** a run under an authored label shows no harm
- **THEN** that fact is not reported as a safety result, because the label was chosen rather than measured

#### Scenario: A real classifier's output supplements, never replaces, the worst case

- **WHEN** output from a candidate query→category classifier becomes available
- **THEN** a distribution-weighted harm figure is reported in addition to the worst case, and the gate still fails if the worst case fails

### Requirement: A clean harm result is bounded by the at-risk units observed

The benchmark SHALL report, **per harm channel**, the number of independent at-risk units the
clean result rests on together with the resulting upper bound on the unobserved harm rate, and
SHALL NOT describe a channel as "harm-clean" while its bound is weaker than 25%.

A worst-case envelope is an exact bound *on the rows it was computed over*. Generalizing it to the
queries the assistant will actually receive is a sampling claim, and is subject to the same power
discipline as the benefit claim. With zero harmful units observed out of `n` independent at-risk
units, the 95% upper bound on the true harm rate is approximately `3 / n` (the rule of three),
which needs no new dependency to compute.

The denominator SHALL be the number of **independent** at-risk units, not the row count, and the
unit differs by channel:

- **H1 — non-KB displacement.** The unit is the distinct non-KB gold page. The bank carries
  **zero** today, so H1 is entirely unobserved and its bound is undefined.
- **H2 — refusal context injection.** The unit is the `should_refuse` anchor. The bank carries
  **3**, giving a bound of ~100%: the current bank can establish nothing about H2 even under a
  perfectly executed sweep.
- **H3 — in-KB misrouting.** The unit is the gold **article**, not the gold row, because the boost
  moves an article's rows together. The bank's 18 gold rows collapse to **7 articles**, giving a
  bound of ~43% — the strongest harm evidence available today, and still short of "harm-clean".

A "harm-clean" statement on a channel therefore REQUIRES `n >= 12` independent at-risk units for
that channel (bound <= 25%), and `n >= 30` (bound <= 10%) SHALL be stated as the target required
before the feature may be enabled in production.

#### Scenario: A channel with no at-risk units reports no bound

- **WHEN** the bank contains no non-KB gold rows
- **THEN** H1 is reported as entirely unobserved with no bound, rather than as clean

#### Scenario: Zero harm observed on three refusal anchors

- **WHEN** the worst-case sweep is clean across the bank's three `should_refuse` anchors
- **THEN** the run reports the H2 bound as ~100% and explicitly declines to call refusal behavior harm-clean

#### Scenario: The misrouting bound uses articles, not rows

- **WHEN** the H3 bound is computed over 18 gold rows resolving to 7 articles
- **THEN** the denominator is 7, not 18, because a category boost moves every row of an article together

#### Scenario: The harm bound travels with the verdict

- **WHEN** any harm statement is written
- **THEN** it carries its channel's at-risk unit count and rule-of-three upper bound, so the strength of the claim is legible rather than implied

### Requirement: Counter-metrics reported with every run

The benchmark SHALL report two counter-metrics alongside every headline metric on every run, and
a treatment result SHALL be inadmissible if reported without them. This is because a retrieval
treatment can improve headline recall while silently harming a subset of the corpus or a class of
question.

- **`non_kb_share@k`** — the fraction of returned top-k documents that are not FASRC KB pages (a
  KB page is any document under `docs.rc.fas.harvard.edu/kb/`). Only KB pages carry an Echo-KB
  breadcrumb, so a non-KB chunk has no `category` key at all and the boost predicate is false for
  it at every category and every weight. A boost a document can never receive is arithmetically a
  penalty on that document.

  **`non_kb_share@k` is a sized diagnostic, not a gate, and it SHALL be labelled as such.** It has
  no "materiality" threshold, because a threshold on it would be a number nobody can derive: a
  non-KB document displaced from the top-k of a row whose gold source is *KB* is not known to be a
  correct document, so its displacement is not a demonstrated harm. It SHALL be reported as
  baseline vs boosted, per row and pooled, at the worst category per row, **together with the
  exact count of non-KB documents displaced from the top-k**, so the size of the arithmetic penalty
  is quantified.

  The **gate** on non-KB demotion is harm channel **H1** — a *gold* (i.e. verified-correct) non-KB
  source present in the baseline top-k and absent from the boosted top-k. H1 is a per-row binary
  witness with no threshold to argue about, and it is the only non-KB trigger that may halt
  anything.

  The record SHALL state plainly that on the current bank **H1 has zero at-risk units** (no bank
  row carries a non-KB gold source), so `non_kb_share@k` is today **reportable but unfalsifiable**
  as a gate — it can move, but no movement of it can be scored as harm. That is exactly the blind
  spot the Phase B non-KB gold rows exist to close, and stating it is a requirement, not a caveat.
- **`refusal_confidence`** — the manufactured-context signal on `should_refuse` anchors. It SHALL
  be reported as (i) the **count of documents in the boosted top-k that are KB pages carrying the
  boosted category and were absent from the baseline top-k**, and (ii) the **baseline, pre-boost
  rerank scores** of the documents in the boosted top-k.

The post-boost score of a matched chunk SHALL NOT be reported as `refusal_confidence`. The boost
adds `w` to every matched chunk by definition, so "top-k scores rise under the boost" is an
arithmetic identity, not evidence of degraded refusal. Only quantities comparable to baseline on
the same scale — the newly-injected-document count and the pre-boost scores of the boosted top-k —
can distinguish manufactured confidence from the trivial offset.

Both SHALL be read from the **adversarial worst-case category sweep**, reported at the worst
category per row, and never from an oracle run and never from a single authored label. Under the
oracle the at-risk rows are never misrouted and never boosted, so a stable oracle counter-metric is
vacuous and SHALL NOT be reported as a safety result.

Both SHALL be reported together with their channel's at-risk unit count and rule-of-three bound.

#### Scenario: A verified-correct non-KB document is displaced

- **WHEN** a bank row whose gold source is non-KB has that gold source in the baseline top-k and absent from the boosted top-k at an admissible weight
- **THEN** the H1 harm event fires and is gated, because the displaced document is known to be correct — this is the trigger, not a movement in `non_kb_share@k`

#### Scenario: The non-KB share is sized, not thresholded

- **WHEN** `non_kb_share@k` falls under the boost on rows whose gold source is KB
- **THEN** the run reports the drop and the exact count of displaced non-KB documents as a diagnostic quantifying the arithmetic penalty, and explicitly does not score it as a harm event, because no displaced document there is known to be correct

#### Scenario: The counter-metric declares its own unfalsifiability

- **WHEN** `non_kb_share@k` is reported on a bank carrying no non-KB gold rows
- **THEN** the record states that H1 has zero at-risk units, that no movement in `non_kb_share@k` can be scored as harm on this bank, and that closing this blind spot is a Phase B prerequisite

#### Scenario: A treatment manufactures context on out-of-scope questions

- **WHEN** the boosted top-k for a `should_refuse` anchor contains category-matched KB documents that were absent from the baseline top-k
- **THEN** the run is reported as degrading refusal, because newly-injected in-corpus context makes a fabricated answer more likely

#### Scenario: The trivial score offset is not mistaken for evidence

- **WHEN** `refusal_confidence` is computed
- **THEN** it is not the post-boost score of the boosted documents, because that quantity rises by `w` by construction and would report the boost's own arithmetic as a finding

#### Scenario: Counter-metrics accompany the headline number

- **WHEN** any benchmark run completes
- **THEN** `non_kb_share@k` and `refusal_confidence` are emitted in the same result record as the primary, secondary, and guardrail metrics, together with the at-risk unit counts and harm bounds

### Requirement: Every reported delta carries its cluster count and a cluster-level interval

The benchmark SHALL treat the gold **article** as the unit of resampling, and SHALL report, with
every baseline-vs-treatment delta: the number of independent gold articles `K`, the number of
distinct captured categories those articles span, the design effect, the effective sample size,
and a cluster bootstrap confidence interval resampled over articles.

Questions are not independent observations. `category` is written identically onto a parent and
every one of its children at ingest (`manager.py:835-843`), so it is an article-level attribute:
the boost adds the same constant to every chunk of an article and moves all of that article's
questions together. Whether an article lands in the top-k is a property of that article's chunk
mass, shared by every question pointing at it — so the intracluster correlation for hit-rate@k is
near 1 by construction, not by assumption.

The bank's 18 gold rows resolve to 7 articles of sizes `[5, 5, 2, 2, 2, 1, 1]`; `running-jobs` and
`cluster-storage` carry five questions each, 56% of the gold rows between them. With
`design effect = 1 + (m̄ − 1) · ICC` and `m̄ = 2.57`, the effective sample size is about **7**, not
18 — and about **5** at the level of the treatment unit (the category), which is what the boost is
actually applied to.

The interval SHALL be computed with the **ratio estimator** — the sum of per-article delta sums
over the sum of per-article row counts — and never as the mean of per-article means, because the
clusters are badly unequal and mean-of-means silently reweights a one-question article equal to a
five-question one, changing the estimand. It SHALL be computed on the **paired** per-row delta
across the two arms, not on the two arms independently.

No new dependency is required: `numpy`, `scipy`, and `pandas` are already direct pins in
`requirements/requirements-base.txt`. `statsmodels` SHALL NOT be introduced, and `sklearn` SHALL
NOT be imported, as it is only a transitive dependency and is pinned nowhere.

Where the number of distinct treatment categories is smaller than `K`, the record SHALL state the
category count and SHALL state that the article-level interval therefore **overstates** the
available independence. Where `K` is below the coverage minimums, the interval SHALL be printed
with an explicit warning that its nominal coverage is not its actual coverage.

#### Scenario: A delta is reported without its cluster count

- **WHEN** a result record contains a delta but no `K`, no effective sample size, and no interval
- **THEN** the record is invalid and the run is reported as failing its own reporting contract

#### Scenario: Unequal clusters are aggregated with the ratio estimator

- **WHEN** the cluster bootstrap is computed over articles carrying 5, 5, 2, 2, 2, 1 and 1 questions
- **THEN** the ratio estimator is used, so a one-question article is not silently weighted equal to a five-question article

#### Scenario: Category-level dependence is disclosed

- **WHEN** the gold articles collapse to fewer distinct categories than there are articles
- **THEN** the record states the category count and warns that the article-level interval overstates the available independence

#### Scenario: An interval below the minimums carries its coverage warning

- **WHEN** an interval is computed on a bank below the coverage minimums
- **THEN** it is printed with the cluster count, the cluster-size vector, the effective sample size, and an explicit warning that its nominal coverage is not its actual coverage

### Requirement: No kill or advance decision may be drawn from an underpowered bank

The benchmark SHALL refuse to issue a kill or an advance verdict on the category boost until the
question bank meets stated coverage minimums, and at any smaller size SHALL report the inferential
evidence as inadmissible in **both** directions.

At `K = 7` gold articles the instrument is broken in three independent ways, each sufficient on
its own:

- **The interval does not mean what it says.** A nominal 95% cluster bootstrap interval delivers
  materially less than 95% actual coverage at `K = 7`, and the shortfall persists well past
  `K = 20`. Coverage is the precondition for power, so any pass/fail rule built on that interval is
  not the rule it claims to be. This failure is driven by the cluster count alone.
- **Significance is combinatorially out of reach.** A paired sign test over 7 clusters clears
  p < 0.05 only on a 7/7 unanimous result (p = 0.0156); 6/7 gives p = 0.125. One article moving
  the wrong way makes significance unreachable at any effect size. This is closed-form binomial
  arithmetic and needs no simulation.
- **The minimum detectable effect is implausible.** On the bank's real cluster sizes, a plausible
  hit-rate delta has power far below the conventional 80% floor.

**The coverage and power figures SHALL be computed, not asserted.** The bank-size minimums below
rest on them, so they SHALL be produced by a **deterministic, fixed-seed Monte-Carlo simulation**
shipped with the benchmark (numpy is already a direct pin; no dependency is added), which estimates
(i) the actual coverage of the nominal-95% article-level cluster bootstrap at `K = 7, 12, 20, 30`
on the bank's real cluster-size vector, and (ii) the power of the paired cluster test at a stated
effect size. The simulation's **assumptions SHALL be published with its output** — seed,
cluster-size vector, assumed ICC, assumed base rate, number of replicates — so that a reader can
re-run it and disagree with it.

Any coverage or power number quoted in a report, a spec, or a decision record SHALL cite that
simulation's output. Working figures of ~83% / ~86% / ~92% / ~94% coverage at `K = 7 / 12 / 20 /
30`, and ~23% power at a +14pp hit-rate delta, are recorded here as **provisional and pending the
simulation**; they SHALL be replaced by its output. The `>= 30`-article minimum SHALL be
**re-derived** from that output. If the simulation contradicts the working figures, the minimum
moves and this requirement is corrected — the minimum may be changed **only** by a published,
seeded re-derivation, never by judgment or by convenience.

The closed-form quantities — the sign-test p-values above and the rule-of-three bound `3 / n` —
are exact arithmetic and are exempt from the simulation obligation.

Therefore:

- Before any confidence-interval-based or significance-based rule may be applied, the bank SHALL
  carry **>= 30 distinct gold KB articles** spanning **>= 6 distinct captured categories**, with
  **no single article contributing more than 10% of gold rows**, and **>= 12 independent at-risk
  units per harm channel** for the harm gate.
- Below those minimums, a null delta SHALL NOT be reported as evidence against the feature, and a
  positive delta SHALL NOT be reported as evidence for it. The earlier asymmetry — a null is
  decisive, a positive is provisional — is **withdrawn**: at this cluster count neither direction
  carries information, and a "decisive null" claim is unfalsifiable because the null is the only
  outcome the instrument can produce.
- Bank expansion is a **prerequisite** of the decision, not a follow-up to it.
- Non-KB gold rows (SchedMD, wiki) SHALL NOT be counted toward the benefit-side minimum. A non-KB
  document can never carry a category and can never be boosted, so it adds zero benefit-side
  clusters; those rows serve the harm gate only. Harm visibility and benefit-side power are
  distinct problems and SHALL NOT be conflated. The change's planned non-KB additions raise `K`
  only from about 7 to about 13, which is still inside the broken-coverage regime, and the report
  SHALL say so rather than present them as satisfying the minimums.

#### Scenario: A null result on the current bank

- **WHEN** the oracle sweep on a bank of 7 gold articles shows no material lift
- **THEN** the run reports the result as inadmissible — the instrument cannot detect a plausible effect at this cluster count — and the feature is neither killed nor advanced

#### Scenario: A positive result on the current bank

- **WHEN** the oracle sweep on a bank of 7 gold articles shows material lift
- **THEN** the run reports the result as equally inadmissible, since the nominal 95% interval at `K = 7` does not deliver 95% coverage, and no classifier work is authorized on it

#### Scenario: The power constants are computed, not asserted

- **WHEN** a coverage or power figure is cited in support of the `>= 30`-article minimum
- **THEN** it cites the fixed-seed simulation that produced it, together with the seed, cluster-size vector, assumed ICC, assumed base rate, and replicate count, rather than an unsourced constant

#### Scenario: The minimum moves only by re-derivation

- **WHEN** someone proposes lowering the `>= 30`-article minimum
- **THEN** it may be changed only by a published, seeded re-derivation whose assumptions are stated, and never by an appeal to convenience, schedule, or an operator's judgment that the bank is "probably fine"

#### Scenario: Non-KB additions are not counted as benefit-side coverage

- **WHEN** SchedMD and wiki gold rows are added to the bank
- **THEN** the coverage report credits them to the at-risk minimum only, and the distinct-KB-article count used for the benefit-side minimum is unchanged

### Requirement: Decision authority is bounded by the class of evidence

The benchmark SHALL classify every finding as corpus-deductive, bank-structural, or inferential,
and SHALL grant each class only the decision authority that class can carry.

The governing asymmetry SHALL be stated in the report: **harm is a minimum and benefit is a
mean.** A harm claim needs one witness — a single (row, category, weight) cell in which the
treatment displaces a correct document is an entailment and requires no statistical power. A
benefit claim needs a population average, which this bank cannot supply. That is why a clean
worst-case sweep can certify safety at a cluster count at which no benefit delta means anything —
and why a clean sweep still cannot be *generalized* beyond the at-risk units it observed.

- **Corpus-deductive** findings are properties of the ingested corpus and do not depend on the
  question bank at all. Category coverage and the measured category vocabulary are the only such
  findings: if `coverage_KB` falls below the pre-registered floor of **0.50**, or the measured
  vocabulary has fewer than **2** labels (see "Corpus category census precedes the sweep and may
  block it"), the boost predicate is false almost everywhere or is constant, and the feature is
  inert on the corpus itself. This finding MAY halt the work at any bank size, and SHALL be phrased
  as a defect in the **extractor**, not a verdict on the **idea**, until extraction is fixed or the
  experiment is explicitly scoped to the covered subset.
- **Bank-structural** findings are exact properties of the captured candidate pools for the
  measured queries — zero headroom, structural inertness, worst-case harm events, and
  `W_safe <= W_benefit`. They carry no sampling error, but they generalize only to the query
  distribution the bank represents. Below the coverage minimums they SHALL be reported as
  diagnostics and hypotheses, explicitly NOT as decisions — **except** that a harm event **that
  fires the halt trigger** (`W_benefit` finite and `W_safe <= W_benefit`, or harm reachable while
  `W_benefit` is `+∞`, both at admissible weights) SHALL always halt the "proceed without a
  classifier" path, because it is a witness rather than an estimate. A harm event *above* the
  benefit onset does not halt; it bounds the operating window. A harm event in the degenerate
  regime (`w >= W_LEX`) is not a harm event at all for gating purposes. At or above the minimums
  these findings MAY kill the feature, and the kill SHALL name the retrieval configuration it is
  scoped to (`candidate_pool_size`, retriever weights, reranker model).
- **Inferential** findings are estimates of a population effect — Δ hit-rate@k, ΔMRR, and their
  intervals. They carry **no** decision authority in either direction below the coverage minimums.

A verdict that cites a finding outside that finding's authority SHALL be rejected in review.

#### Scenario: The corpus is barely categorized

- **WHEN** `coverage_KB` falls below the pre-registered floor of 0.50, or the measured vocabulary has fewer than 2 labels
- **THEN** no sweep is run, the verdict is `CORPUS_BLOCKED`, and the finding is recorded as a defect in extraction rather than as a verdict on the boost

#### Scenario: A structural finding on a thin bank

- **WHEN** the headroom precheck shows zero possible lift on a bank of 7 gold articles
- **THEN** `NO_HEADROOM` is recorded as a statement about the instrument on this bank at this retrieval configuration, and is reported as a hypothesis to be retested on the expanded bank rather than as a kill of the feature

#### Scenario: A structural finding on an adequate bank

- **WHEN** the same zero-headroom result is reproduced on a bank meeting the coverage minimums
- **THEN** it MAY kill the feature, and the kill statement names the retrieval configuration it is scoped to

#### Scenario: A harm witness that precedes benefit is admissible on a thin bank

- **WHEN** the worst-case sweep demonstrates a harm event at an admissible weight at or below `W_benefit` on a bank of 7 gold articles
- **THEN** it halts the "proceed without a classifier" path despite the bank's size, because one witnessed cell is an entailment rather than an estimate of a population mean

#### Scenario: A harm witness that follows benefit does not halt

- **WHEN** the worst-case sweep's lowest admissible harm weight strictly exceeds `W_benefit`
- **THEN** the finding is reported as the upper bound of the operating window, and the no-classifier path is not halted by it, because the halt trigger is harm *preceding* benefit and not the mere existence of a harm cell somewhere on the weight axis

#### Scenario: A verdict overreaches its evidence class

- **WHEN** a write-up cites an inferential delta from an underpowered bank as grounds to build or drop the classifier
- **THEN** the verdict is rejected in review, because that finding class carries no decision authority at that cluster count

### Requirement: The result record carries an explicit verdict that defaults to INDETERMINATE

Every result record SHALL carry a `verdict` field whose default value is **`INDETERMINATE`**, and
that value SHALL stand unless one of the enumerated non-inferential conditions below is met. A
benefit delta — positive or null — SHALL NOT move it.

The admissible verdicts are exactly:

- **`INDETERMINATE`** (default) — the run reports measurements, prechecks, and diagnostics, and
  decides nothing. This is the expected outcome on the current bank.
- **`CORPUS_BLOCKED`** — the corpus category census shows `coverage_KB < 0.50`, or a measured
  vocabulary of fewer than 2 labels. The thresholds are the pre-registered ones and are not an
  operator judgment. Bank-independent, so it stands at any `K`. It is a verdict on the extractor,
  not on the idea.
- **`NO_HEADROOM`** — `max_possible_lift(MRR)` is zero: every gold source present in the candidate
  pool is already at rank 1 of the returned top-k. This entails `max_possible_lift(hit@k) = 0` and
  `W_benefit = +∞`, so no weight of any size can help any row of this bank. It is analytic rather
  than statistical, and it is scoped to the retrieval configuration measured. Its **meaning is
  scoped by bank size**: below the coverage minimums it records that *this bank* cannot observe
  lift at *this* retrieval configuration and is a hypothesis to retest, not a kill of the feature;
  at or above the minimums, reproduced, it MAY kill (naming its configuration). Recording it never
  suppresses the harm sweep.
- **`HARM_REACHABLE`** — the adversarial worst-case sweep demonstrates a harm event at an
  **admissible** weight (`0 < w < W_LEX`) that satisfies the single halt trigger: `W_benefit` is
  finite and `W_safe <= W_benefit`, or harm is reachable while `W_benefit` is `+∞`. A harm event at
  a weight *above* `W_benefit` bounds the operating window and does **not** produce this verdict; a
  harm event in the degenerate regime (`w >= W_LEX`) is not admissible evidence at all. This
  **halts** the "proceed without a classifier" path. It is not a kill: safety may still be certified
  against a real candidate classifier's output distribution.

Where more than one condition holds, every applicable verdict SHALL be recorded, and
`HARM_REACHABLE` SHALL be reported as the operative one, because it is the only verdict that halts
a path rather than describing an instrument.

Absence of demonstrated harm SHALL NOT be recorded as safety, and no verdict SHALL be derived from
it — the instrument can witness harm but it cannot clear it beyond the at-risk units it observed
and beyond the single-label scope condition on the certificate.

The write-up SHALL name the successor change that will make the kill/advance decision
(`decide-category-boost`) and SHALL state its entry conditions — the bank coverage minimums, and
the single-label hard-match scope condition under which this change's safety certificate transfers
— so that the deferral is a scheduled next step with a published trigger rather than an open-ended
postponement.

#### Scenario: A null benefit result leaves the verdict untouched

- **WHEN** the oracle sweep shows no material lift at any weight on a bank below the coverage minimums
- **THEN** the record reports the null with its interval, its effective sample size, and the prechecks' structural explanations, and the verdict field remains `INDETERMINATE`

#### Scenario: A positive benefit result leaves the verdict untouched

- **WHEN** the oracle sweep shows apparent lift on a bank below the coverage minimums
- **THEN** the record reports the lift with its interval and coverage warning, the verdict field remains `INDETERMINATE`, and no classifier work is authorized by it

#### Scenario: Demonstrated harm at or below the benefit onset halts the no-classifier path

- **WHEN** the worst-case sweep triggers a harm event at an admissible weight at or below `W_benefit`
- **THEN** the verdict is `HARM_REACHABLE`, the arg-max category is named, and the change does not proceed to a classifier on an assumed label

#### Scenario: A harm cell above the benefit onset does not move the verdict

- **WHEN** the only admissible harm weights lie strictly above `W_benefit`
- **THEN** the verdict is not `HARM_REACHABLE`, the operating window `[W_benefit, W_safe)` is reported instead, and the record remains `INDETERMINATE` because a window on an underpowered bank licenses only bank expansion

#### Scenario: Absence of harm is never reported as safety

- **WHEN** the worst-case sweep finds no harm event
- **THEN** the record states that harm was not demonstrated on these at-risk units at this `k`, reports the per-channel rule-of-three bounds, states the single-label hard-match scope condition, and explicitly declines to state that the treatment is safe

#### Scenario: The decision is deferred to a named successor with a published trigger

- **WHEN** the results are written up
- **THEN** the write-up names `decide-category-boost` as the change that will decide the feature, states the bank coverage minimums that gate its start, and states that the safety certificate transfers only to a single-label hard-match boost

### Requirement: Results are reported per slice, never as a single pooled number

The benchmark SHALL report every metric sliced by anchor type, by gold article, by captured
category, and by source group, in addition to any pooled figure; a pooled figure SHALL NOT be
reported on its own.

Pooling hides exactly the failure modes this change exists to detect. Ten of the bank's 18 gold
rows are `easy_retrieve` anchors that the project documents as ceiling-pinned, so pooling them with
the 8 reasoning rows drags any real effect toward zero and disguises saturation as absence of
effect. Two articles carry 56% of the gold rows, so a pooled delta is substantially a statement
about those two articles. Non-KB rows can never be boosted, so pooling them with KB rows dilutes
both the benefit and the harm signal.

Each slice SHALL carry its own row count and article count. A slice spanning fewer than 3 articles
SHALL be reported as descriptive only, and SHALL NOT carry an interval or a verdict.

#### Scenario: Saturated anchors are separated from reasoning anchors

- **WHEN** results are reported
- **THEN** `easy_retrieve` and `reasoning` rows carry their own metrics and counts, so a null pooled delta driven by saturated easy rows is visible rather than hidden

#### Scenario: A dominant article is visible in the slices

- **WHEN** one article contributes a large share of the gold rows
- **THEN** the per-article slice shows its share, so a pooled delta cannot be read as a general effect

#### Scenario: A thin slice is not given a verdict

- **WHEN** a slice spans fewer than 3 gold articles
- **THEN** it is reported descriptively, without an interval and without a pass/fail statement
