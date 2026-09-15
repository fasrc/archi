> # ⏸️ SHELVED — do not implement
>
> **Decision (2026-07-13): the category boost is not being built, and this instrument is not
> being built either.** Loop 1 completed and this plan is kept for the record; Loop 2 was
> never started. Do not run `/opsx:apply` on it.
>
> **Why the feature was dropped.** Five independent strikes, none of which got weaker under
> review:
> 1. The category is mostly a restatement of words the query already contains — which BM25 and
>    the embedder already use. Near-zero information gain on a typical query.
> 2. It is anti-correlated with need: it would help most where the query's wording does *not*
>    match the article, which is exactly where a classifier reading those same words also fails.
> 3. The FASRCDocsAgent already runs a FlashRank cross-encoder with full query–passage
>    attention — a strictly richer version of what a 19-bucket match approximates.
> 4. Only KB pages carry a breadcrumb, so SchedMD/wiki chunks can *never* be boosted. A boost a
>    document can never receive is arithmetically a penalty on it.
> 5. It can manufacture confident context for out-of-scope questions, converting refusals into
>    confident fabrications.
>
> **Why the instrument was dropped too.** The RAGAS benchmark (`retrieval-benchmarking`,
> `src/bin/service_benchmark.py`) already exists to decide whether an idea works. Building a
> second, parallel measuring apparatus to adjudicate this one feature was scaffolding we did not
> need. If the boost is ever revisited, run it as a RAGAS A/B arm.
>
> **What this analysis DID find, and it outlives the shelved feature.** Two real defects in the
> evaluation set itself, which affect **every** retrieval change (chunking, reranking,
> embeddings), not just this one:
>
> - **The bank is ceiling-pinned, so improvements are undetectable.** 18 gold rows resolve to
>   only **7 distinct articles** (`running-jobs` + `cluster-storage` = 56% of them), and 10 of
>   the 18 are `easy_retrieve` anchors the bank itself documents as trivially retrievable. With
>   almost no headroom left, a genuinely good retrieval change measures as "no change." And the
>   unit of independent evidence is the *article*, not the question — a change moves an article's
>   rows together — so the effective sample size is ~7, not 18.
> - **Zero non-KB gold sources, so regressions are undetectable.** Every gold source in every
>   bank is a KB page; none live in `slurm.schedmd.com` or the namesake wiki page. Any change
>   that lifts KB recall *by demoting the SchedMD docs* would measure as a clean win while
>   silently regressing a third of the corpus.
>
> Fixing the bank is the prerequisite for trusting **any** future retrieval result, including a
> RAGAS A/B of this feature if it is ever revived.

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
query→category mapping and see whether it beats baseline.

The labels are free. `fasrc_ragas_queries.json` questions carry gold source URLs (the
human-verified answer location); #97 gives article → category. Compose them and you get
query → true category with zero hand-labeling.

**But the bank cannot carry the decision, and pretending otherwise is the biggest risk here.**
The unit of independent evidence for a retrieval treatment is the gold **article**, not the
question: `category` is written identically onto a parent and every one of its children at ingest,
so the boost moves all of an article's questions together. The bank's 18 gold rows resolve to
**7 articles** (`running-jobs` + `cluster-storage` = 56% of them), collapsing to ~5 categories —
and the *category* is what the boost is actually applied to.

At K=7: a nominal 95% cluster-bootstrap interval delivers **~83% real coverage** (the interval
lies about its own confidence); a paired sign test clears p<0.05 **only on a 7/7 unanimous
result**; and a +14pp hit-rate delta has **~23% power**. Worse, 10 of the 18 gold rows are
`easy_retrieve` anchors the repo documents as ceiling-pinned, and a paired binary hit@5 test is an
exact sign test — so **once baseline hit@5 ≥ 15/18, a significant benefit is mathematically
impossible at any `w`, for any feature.** A "decisive null" on this instrument is unfalsifiable:
the null is the only outcome it can produce.

So this change ships an **instrument and a diagnosis, not a verdict**. Bank expansion is a
*prerequisite* of the decision, not a follow-up to it.

## What Changes

- **New: a retrieval-only benchmark.** Scores retrieval directly — hit-rate@k and MRR against
  each question's gold sources — with **no LLM call and no answer generation**, so it runs
  in-process against a live corpus and can sweep a scoring parameter without a redeploy per arm.
- **New: two mandatory counter-metrics**, reported with every run. A boost result is
  inadmissible without them, because the current bank is structurally blind to the harms the
  boost risks:
  - `non_kb_share@k` — fraction of top-k that is not a KB page. Only KB pages have a
    breadcrumb, so slurm/wiki chunks have no category *permanently*. A boost they can never
    receive is arithmetically a **penalty on the entire non-KB corpus**. It is a **sized
    diagnostic, not a gate**: it gets no "materiality" threshold, because a non-KB doc displaced
    from a *KB-gold* row's top-k is not known to be correct. The gate is harm channel **H1** — a
    *verified-correct* non-KB gold source dropping out of the top-k — a per-row binary witness with
    nothing to argue about. And the record must say plainly that H1 has **zero** at-risk units on
    today's bank, so `non_kb_share@k` is currently reportable but **unfalsifiable** as a gate. That
    is the blind spot, stated as a finding rather than hidden by a metric that cannot fail.
  - `refusal_confidence` on `should_refuse` anchors. A classifier will happily map *"GPU partition
    layout on MIT's Engaging cluster"* to `Cluster Usage`; the boost then promotes confident FASRC
    context for a question the bot should decline. **The boost can manufacture false confidence**,
    turning refusals into confident wrong answers. Note the metric is **not** the post-boost score
    mass — the boost adds `w` to every matched chunk *by definition*, so "scores rise" is an
    arithmetic identity, not a finding. It is the count of category-matched KB docs the boost
    **newly injected** into the top-k, plus the **pre-boost** scores of what it promoted.
- **New: single-retrieval pool capture + offline replay.** Everything upstream of the boost seam is
  invariant to the boost — the candidate pool depends only on the query, and `_rerank` literally
  blanks the metadata channel (`"meta": {}`) before scoring, so FlashRank cannot see `category`.
  The one DB call after the seam is a set-membership lookup whose id set is built by an exhaustive
  loop over the whole pool. So: capture the 20-candidate pool once per query, pre-fetch all
  parents once, and compute **every (category × weight) cell** by in-memory replay. A parity test
  at `w=0` must reproduce production's top-5 exactly, or the numbers are void.
- **New: harm is gated on an adversarial worst-case sweep, with no authored label anywhere.** The
  first draft proposed a "simulated-classifier" mode whose boost category was an authored
  per-question `assumed_category`. **That is rejected and deleted**: a charitable authored label
  makes the harm metrics look stable even when a real classifier would route the at-risk query
  somewhere more damaging — it would make our own judgment the safety oracle. Instead, sweep
  **every** measured in-KB category against **every** row and gate on the **worst** cell, naming
  the arg-max category and emitting the full matrix. Nobody picks the label, so nobody picks how
  much harm the experiment can find. The pool capture makes this free (one ≤20-element sort per
  cell, zero retrieval/DB/ONNX), so cost is no excuse for a one-label gate. If an authored label
  ever exists it is **non-normative** — an annotation on the harm surface, never a gate.
- **New: a third harm channel — in-KB misrouting (H3).** The draft had two (non-KB displacement,
  refusal injection). The likeliest real harm was missing: a classifier assigns `Storage` to a
  Running-Jobs question, and the boost demotes the **correct KB article** out of the top-k. It is
  another cell of the same cached grid, so it costs nothing — and it turns every gold row into an
  at-risk row, which is the *only* reason the harm side has any observational base on today's bank
  (rule of three, per channel: H1 has **0** units, H2 has **3**, H3 has **7** articles).
- **New: the gate is asymmetric, because harm is a MIN and benefit is a MEAN.** One witnessed
  (row × category × weight) cell is an *entailment* — no statistical power required. So a **clean**
  worst case is a genuine safety certificate (subject to the single-label scope condition below),
  and a **dirty** worst case — one firing the weight-conditioned trigger — **halts** the "proceed
  without a classifier" path rather than killing the feature: harm is *reachable*, not proven, and a
  category no real classifier would emit must not become a false-kill.
- **New: prechecks that can invalidate the instrument before it is used.** Both fall out of one
  baseline run. *Headroom:* the boost reorders only inside the pool, so
  `max_possible_lift(hit@5) = pool_recall@20 − baseline_hit@5` and
  `max_possible_lift(MRR) = MRR_pool_ceiling − baseline_MRR`, exactly. MRR headroom is the strictly
  stronger of the two and is the single trigger: it is zero exactly when every in-pool gold source
  already ranks first, which entails zero hit@5 headroom *and* `W_benefit = ∞` — no weight of any
  size can help. That is `NO_HEADROOM`, and it stops the *interpretation of a benefit null*, not the
  run: the harm sweep still executes, because harm is what can halt the feature. *Degeneracy:* a
  uniform additive offset is order-preserving, so a category-homogeneous pool makes the boost
  structurally inert at any `w`.
- **New: metric hierarchy, and an operating window.** Primary is **min-weight-to-flip** — the boost
  is additive and monotone, so the top-k is a step function of `w` whose breakpoints are the
  pairwise rerank-score gaps; the whole sweep is exact arithmetic over the capture, has **no
  ceiling**, and stays informative where hit@k goes blind. From it come `W_safe` (the lowest
  admissible weight at which *any* row suffers *any* harm channel under *any* category, `+∞` if
  none) and `W_benefit = min(W_benefit_hit, W_benefit_rank)` — the lowest admissible weight that
  does anything good for any gold row, whether by promoting a missed gold source **into** the top-k
  or by **improving the rank** of one already in it. Both components take `+∞` when their row set is
  empty, an explicit convention: without it, a hit-saturated bank (which this one is expected to be)
  has an empty baseline-miss set, `min` over ∅ collapses, and the headline "the feature is
  dominated" fires *by construction* in exactly the regime the primary readout exists to survive.
  So: **if `W_benefit` is finite and `W_safe ≤ W_benefit`, no admissible weight helps any question
  in this bank before worst-case harm becomes reachable** — reported with no CI, because it is an
  entailment, and labelled *optimistic* (`W_safe` is a min over instrumented rows, so more at-risk
  rows can only lower it). If `W_benefit = ∞`, the run reports "no benefit reachable", **not**
  "dominated". MRR is the secondary (inferential) metric; **hit-rate@k is a guardrail only**.
  (`recall@k` is not an option: no row has more than one gold source, so it is identical to hit@k
  here.)
- **New: cluster-level uncertainty or none at all.** Every delta carries `K` (distinct gold
  articles), the category count, the design effect, the ESS, and a cluster bootstrap CI resampled
  over **articles** with the **ratio estimator** (mean-of-means would silently reweight a
  1-question article equal to a 5-question one). Per-slice reporting by anchor type, article,
  category, and source group — a pooled number alone is not admissible.
- **Withdrawn: the asymmetric decision gate. Replaced by an explicit verdict field.** "A null is
  decisive, a positive is provisional" was backwards — at K=7 *neither* direction carries
  information. Every result record now carries `verdict`, defaulting to **`INDETERMINATE`**, and a
  benefit delta (positive *or* null) **cannot move it**. Only three non-inferential conditions can,
  each on a **pre-registered numeric trigger** rather than an operator's judgment: `CORPUS_BLOCKED`
  (the corpus census — `coverage_KB < 0.50` or a measured vocabulary of fewer than 2 labels; a floor
  fixed *before* the census is read, because "is coverage too low?" left to post-hoc judgment would
  put the author back in the oracle's chair; bank-independent, so it stands at any K), `NO_HEADROOM`
  (`max_possible_lift(MRR) = 0`; analytic, config-scoped, and below the coverage minimums a
  hypothesis about *this bank* rather than a kill), and `HARM_REACHABLE` (a witnessed harm cell at
  an admissible weight *at or below* `W_benefit` ⇒ halt). **Absence of demonstrated harm is never
  recorded as safety.** Findings are graded by class —
  **corpus-deductive** (category coverage and the measured vocabulary), **bank-structural**
  (headroom, inertness, a witnessed harm cell, `W_safe ≤ W_benefit`), **inferential** (Δhit@k,
  ΔMRR; **no** authority below the minimums) — and each gets only the authority its class can carry.
- **New: URL reconciliation.** Authored bank URLs and ingested `documents.url` differ in form —
  the bank's own README warns SOURCES mode "needs URL reconciliation." Exact-string lookup would
  score a retrieved gold article as a miss *and* silently resolve the oracle to no category, so
  the sweep would measure **URL-format drift** rather than the boost. Canonicalize both sides;
  report unresolved gold sources rather than swallowing them.
- **Modified: the question bank carries coverage minimums, and they are a prerequisite.** Two
  distinct obligations that must not be conflated:
  - *Source-group coverage (harm visibility).* Today every gold source in every bank is a KB page —
    **zero** in `slurm.schedmd.com`, none on the namesake wiki page. Add SchedMD- and wiki-answered
    questions in the modern `user_input`/`reference` dialect.
  - *Benefit-side power (decidability).* **≥30 distinct gold KB articles**, **≥6 categories**, **no
    article >10% of gold rows**; and **≥12 at-risk rows** for the harm gate (rule of three: with
    `n=3` today, the 95% upper bound on the unobserved harm rate is ~100%).

  The ~6 non-KB additions serve the **first** obligation only — a non-KB doc can never carry a
  category and can never be boosted, so it adds **zero** benefit-side clusters. One fix, one
  problem. Both obligations are discharged in **Phase B**; this change edits no bank.
- **New: the swept weight is bounded, and the halt trigger is weight-conditioned.** Rerank scores
  are bounded, so at a large enough `w` an additive boost is **lexicographic** — every
  category-matched chunk above every unmatched one — and a harm cell becomes an arithmetic
  certainty. An unbounded sweep with an "any harm cell halts" rule is therefore a gate that *always
  fires*: the mirror image of the "decisive null" this proposal withdraws. So the sweep is confined
  to a **derived** operating range `0 < w < W_LEX`, where `W_LEX` is the smallest weight at which
  the boost goes lexicographic on any bank query; weights above it are the "degenerate regime" (a
  hard category preference — an explicit non-goal) and gate nothing. And the halt has **one**
  trigger, used identically everywhere: **`W_benefit` finite and `W_safe ≤ W_benefit`** — harm
  reachable at or *before* the first weight that helps anything. Harm *above* `W_benefit` bounds the
  operating window rather than halting; that is precisely the case in which the feature is usable.
- **New: the safety certificate carries a binding scope condition.** Worst-case-over-single-
  categories is an upper bound on harm **only** for a boost that adds `w` to exactly one category
  per query under a hard-match predicate. It is **not** an upper bound for a multi-label boost or a
  soft `w · P(c | q)` boost — there the displaced set is the **union** over boosted categories and
  can exceed any single category's harm — and *both* classifier candidates on the table
  (embedding-affinity centroids; an agent tool argument) naturally emit a distribution. So the
  certificate ships as a precondition on `decide-category-boost`: single-label hard-match ⇒ it
  transfers; multi-label or soft ⇒ it does **not**, and the sweep must be re-run over category
  **sets**. Asserting "a real classifier emits exactly one label" as a fact about a classifier that
  does not exist would be a fresh self-flattering hole in the same place as the old one.
- **New: the coverage/power constants are computed, not asserted.** The ≥30-article prerequisite is
  what blocks the decision, so the numbers underwriting it (bootstrap coverage at K=7/12/20/30;
  power at a stated effect size) are produced by a **fixed-seed simulation** shipped with the
  benchmark — `numpy` is already pinned, so it costs no dependency — with seed, cluster sizes,
  assumed ICC, base rate, and replicate count published so a reader can re-run and disagree. The
  minimum may move only by a published seeded re-derivation, never by judgment.
- **Phase A / Phase B, with a named successor so the deferral is bounded.** Phase A (this change)
  builds the instrument and reports diagnostics **on the current, unedited bank**. **It writes no
  kill and no advance.** Phase B expands the bank to the minimums (and adds the non-KB gold rows);
  the kill/advance decision moves to a named successor change, **`decide-category-boost`**, whose
  published entry conditions are exactly those minimums plus the single-label scope condition. This
  is a scheduled next step with a trigger, not an open-ended postponement — and Phase A keeps a live
  halt (harm witness before benefit, zero headroom, corpus census), so the feature can still die
  today on evidence that does not need power. If expansion is never funded, the honest close-out is
  *"harness built, feature undecided"* — not *"feature killed"*.
- **A measuring instrument, not a feature.** This change deliberately ships *no* production
  retrieval behavior.

## Capabilities

### New Capabilities
- `retrieval-only-benchmark`: in-process scoring of retrieval quality against gold sources with no
  answer-generation LLM; a **corpus category census** with a **pre-registered numeric coverage
  floor** that measures the sweep's own category vocabulary and can block the run
  bank-independently; single-retrieval pool capture, taken through an identity-default hook at the
  production ranking seam (which the benchmark overrides to **record**, never to boost), with
  parity-tested offline replay of the ranking tail; URL reconciliation on both sides of every match;
  headroom and degeneracy prechecks that can declare the *inferential* instrument dead before the
  sweep; a **derived bound on the swept weight** (`0 < w < W_LEX`) that excludes the lexicographic
  regime in which a harm cell is arithmetic rather than evidence; a metric hierarchy
  (min-weight-to-flip primary, MRR secondary, hit-rate@k guardrail-only) yielding the
  `W_safe` / `W_benefit` operating window, with explicit empty-set conventions on every minimum; an
  **adversarial worst-case category sweep** over three harm channels (non-KB displacement, refusal
  injection, **in-KB misrouting**) gated on a single weight-conditioned halt trigger, with the
  de-confounded `non_kb_share@k` and `refusal_confidence` counter-metrics read from it; a hard
  prohibition on any authored label feeding a gate; a **binding single-label hard-match scope
  condition** on the safety certificate; per-channel rule-of-three harm bounds; article-level
  cluster bootstrap uncertainty with per-slice reporting and **seeded-simulation-derived** coverage
  and power constants; an explicit bound on which class of finding may kill or advance the feature;
  and an explicit `verdict` field that defaults to `INDETERMINATE`.

### Modified Capabilities
- `retrieval-benchmarking`: the "Grounded FASRC question banks" requirement gains (a) a source-group
  coverage obligation — a bank must include gold sources from **every corpus source group** (KB,
  SchedMD, wiki) so a treatment that harms one group is detectable; (b) **benefit-side and
  harm-side power minimums**, stated in gold *articles* and in per-channel at-risk *units*, below
  which a bank may not be used to justify adopting or rejecting a retrieval treatment; and (c) a
  prohibition on a bank carrying **any authored per-question label from which a safety metric is
  computed** — authored gold sources remain required, because a gold source is a verifiable fact,
  not a prediction about a hypothetical classifier. The requirement text is also brought onto the
  modern RAGAS 0.3.5 dialect (`user_input`/`reference`). "Data-grounded recommendation" is modified
  so that every cited number carries its cluster count, interval, and minimum detectable effect, and
  so that an underpowered bank yields **no** adopt-or-reject recommendation at all.

## Impact

- **New code:** a retrieval-only scorer under `scripts/benchmarking/`. Reuses
  `build_vector_retriever` (`retrievers/factory.py:29`) and the project's own config loading;
  does **not** touch `src/bin/service_benchmark.py` (RAGAS path is unchanged).
- **Data (Phase A): NONE.** This change ships **no** edits to
  `examples/benchmarking/fasrc_ragas_queries.json`. Phase A runs on the bank as it stands — 10
  `easy_retrieve` + 8 `reasoning` (gold-sourced) + 3 `should_refuse` (no gold source, by design —
  they test refusal, not recall) = 18 gold rows over **7 articles** — and *reports its own
  blindness as a finding*. Every count quoted throughout (H1 `n=0`, H2 `n=3`, H3 `n=7`, the
  rule-of-three bounds) is a statement about that bank; editing it in this PR would falsify all of
  them on the day it landed.
- **Data (Phase B, the prerequisite):** *all* bank work. The SchedMD- and wiki-gold questions that
  close the H1 blind spot, **plus** expansion to ≥30 distinct gold KB articles (~78 gold rows)
  across ≥6 categories with no article over 10% of rows, and ≥12 independent at-risk units **per
  harm channel**. **No `assumed_category` field is added** — no authored label may feed a safety
  metric. If one is ever authored it is a non-normative annotation on the harm surface and can gate
  nothing.
- **Statistics:** `numpy==1.26.4`, `scipy==1.13.1`, `pandas==2.3.2` are already direct pins
  (`requirements-base.txt:83,64,68`); the cluster bootstrap is ~15 lines. `statsmodels` is **not
  installed** and must not be added; `sklearn` is transitive-only (pinned nowhere) and must not be
  imported.
- **Credentials:** no *answer-generation* LLM is constructed, but retrieval still embeds the
  query — so a deployment with a hosted embedder still needs that key. The no-key guarantee
  holds end-to-end on dev only because dev uses `HuggingFaceEmbeddings`.
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
