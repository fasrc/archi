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
- Build a durable, LLM-free retrieval scorer that can sweep a scoring parameter against one corpus
  in one process — no redeploy, no re-ingest, no LLM credentials.
- Establish **what the evidence can and cannot carry** before drawing any conclusion from it:
  headroom, degeneracy, effective sample size, minimum detectable effect.
- Make all three harm channels (H1 non-KB displacement, H2 refusal injection, **H3 in-KB
  misrouting**) visible *and* honest about their power — the current bank is blind to H1, nearly
  blind to H2, and would remain underpowered on both even once it can see them.
- Leave production retrieval behaviorally unchanged.

**Explicitly NOT a goal (changed from the first draft):**
- ~~Decide the category-boost feature on a measured number.~~ The bank cannot carry that decision
  (D12). This change produces an instrument, a set of prechecks, and a *diagnosis*. The decision
  is gated on bank expansion.

**Non-Goals:**
- Wiring the soft boost into production (gated on the decision this change produces).
- Choosing between the classifier candidates (embedding-affinity centroids vs. agent tool
  argument — both viable now that `QAPipeline` is dead).
- Any hard metadata filter. The dormant `filter` plumbing at `postgres_vectorstore.py:350`
  stays dormant — and note it interpolates the filter **key** into SQL
  (`f"c.metadata->>'{key}'"`), an injection surface the moment a key becomes model-chosen.
- Touching `src/bin/service_benchmark.py`; the RAGAS path is unchanged.

## Decisions

### D0 — Census the corpus FIRST; it is the only bank-independent finding here
Any sweep result is meaningless **if the corpus is not actually categorized**. If #97's breadcrumb
extractor tagged only a minority of KB articles, the boost predicate is false almost everywhere,
the oracle is largely inert, and Δ≈0 would measure a weak *extractor*, not a bad *idea* — a
confound that would silently invalidate the whole experiment.

So before any sweep, take a **census** (not a sample) of the corpus: the fraction of KB chunks
carrying a non-empty `category` (`coverage_KB`), and the distribution over the observed
vocabulary. Low coverage blocks the sweep, and the finding is written up as a defect in
**extraction**, never as a verdict on the **idea**.

**The floor is a pre-registered number, fixed before the census is read**, because "is coverage
too low?" left to post-hoc judgment would make the author the oracle — the exact failure this
redesign exists to remove:

| `coverage_KB` | Action |
|---|---|
| `< 0.50`, or `|V| < 2` | **`CORPUS_BLOCKED`** — do not sweep |
| `0.50 – 0.80` | sweep, but **explicitly scoped** to the covered subset; uncategorized gold rows excluded and reported; every number carries its coverage label |
| `>= 0.80` | unscoped run |

Why 0.50: below it, most of the KB corpus is — *with respect to the boost predicate* —
indistinguishable from SchedMD. An uncategorized KB chunk can never be boosted, exactly like a
non-KB one, so the "category boost" would mostly be a **labelled-vs-unlabelled** discriminator, a
different feature that penalizes uncategorized KB articles the way H1 penalizes non-KB documents.
Why `|V| >= 2`: with one label the predicate is constant and the boost is a global offset, which
reorders nothing at any `w`.

The census also **defines the sweep's category axis**. The vocabulary is *measured*, not assumed:
it is neither the proposal's claimed 19-label site taxonomy nor the 6-label list in
`deploy/fasrc-dev/config.yaml` — that list is `llm_category`, a *different field* the boost does
not read. Its true cardinality is a property of the ingested corpus and must be reported from it.

Because a census has no sampling error, this is the **one** finding in the change that can halt
the work at any bank size (D12).

### D1 — An identity hook at the rerank seam — the benchmark's *capture* seam, not its boost seam
The boost **must act on the full 20-candidate pool before truncation**. Boosting the returned
top-5 could only reorder them — it could never promote a document from rank 12 into the top 5,
which is precisely the effect being measured. So the seam has to sit inside
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

Production behavior is **bit-for-bit unchanged** (identity), and a test asserts it.

**What the benchmark does with it — and this changed with D8.** In the first draft the benchmark
overrode the hook to *apply* the boost live, once per (category, weight) cell. After D8, every grid
cell is offline replay against a cached pool, so **nothing applies a boost inside a live
retrieval**. The hook's post-revision role is to be the **capture seam**: the benchmark subclasses
the retriever, overrides `_adjust_ranked_scores` to **record** `candidates`, `ranked`, and the
rerank scores, and returns `ranked` *unmodified*. That is exactly the point in the pipeline where
all three of those objects coexist, which is why the hook is the right seam for capture and not
merely for boosting.

So the hook is **not** dead production code with no consumer: it is the one seam through which the
benchmark observes production's ranking, and it carries a requirement of its own (see
"Single-retrieval pool capture with offline replay of the ranking tail" — the bit-for-bit-unchanged
obligation lives there, not only in a task).

Rejected alternatives: copying the whole `_get_relevant_documents` into a benchmark subclass
(duplicates 60 lines and drifts); monkeypatching `_rerank` (fragile, and `_rerank` returns only
`(index, score)` pairs — it does not have the candidate documents the capture needs).

Bonus: this is *exactly* the seam a real soft boost would later occupy, so the experiment validates
the production seam rather than testing something else.

`hierarchical_retriever.py` is black/isort-clean (tree normalized in #69), and the change is a
new method plus one rewritten line — low churn against the ≥80% diff-coverage gate.

### D2 — Retrieval-only scorer, reusing the deployment's own retriever
New script under `scripts/benchmarking/`. It builds the *configured* retriever via
`build_vector_retriever` (`retrievers/factory.py:29`) against the live vectorstore, using the
project's own config loading — **not** hand-read secrets. For each question it invokes the
retriever and scores the returned documents' URLs against `sources`.

No **answer-generation** LLM is constructed, which is what makes the sweep cheap: one process,
one corpus, N weights. Note the guarantee is scoped: retrieval still **embeds the query**, so a
deployment configured with a hosted embedder (`OpenAIEmbeddings`) still needs that provider's
key. It holds end-to-end on FASRC dev only because dev uses `HuggingFaceEmbeddings`
(`deploy/fasrc-dev/config.yaml:84`).

### D3 — Gold category by *canonicalized* URL join, computed once
`gold_category(q)` = the captured `category` of the article at `q.sources[0]`. Build a
`url -> category` map in one query over `documents` ⋈ `document_chunks` at startup, then look
up per question.

**URLs must be canonicalized on both sides before matching or joining.** The bank's own README
warns that SOURCES mode "needs URL reconciliation" — the sitemap-driven SPLIT ingest may store
a slightly different slug than the authored canonical URL, and source-list generation can
collapse trailing slashes. An exact-string lookup would therefore both (a) score a retrieved
gold article as a miss and (b) silently resolve the oracle to *no category* — so the sweep
would be measuring URL-format drift, not the category boost. Any gold source that fails to
resolve to an ingested document is **reported as unresolved**, never silently counted as a
retrieval failure.

The oracle boosts **every chunk sharing the gold article's category**, not the gold article
itself — that is what makes it a simulation of a *perfect classifier* rather than a circular
lookup of the answer.

### D3a — The harm gate is an adversarial worst-case sweep, not an authored label
A gold-category oracle is **benefit-only by construction**: it only ever boosts toward the
correct category, so every at-risk population receives **zero boost at any `w`**, or is by
definition not misrouted —

- `should_refuse` anchors have **no gold source**, so `gold_category(q)` is undefined;
- non-KB gold articles (SchedMD, wiki) have **no captured category** at all — the `category` key
  is simply absent, so `chunk.category == c` is false for every `c` and every `w`;
- a correctly-routed KB row cannot, by construction, demonstrate what a *mis*-routed one does.

The counter-metrics would therefore read "stable" under an oracle sweep no matter how harmful the
real feature is. Adding SchedMD questions does not by itself fix this — under the oracle those
rows are inert.

The first draft answered this with a **simulated-classifier** mode: an authored per-question
`assumed_category` (the in-KB label a plausible classifier would guess), carried by every row.
**That was wrong, and it is now rejected.** An authored single label is a guess about a classifier
that does not exist. A charitable guess makes `non_kb_share@k` and `refusal_confidence` look
stable even when a real classifier would route the at-risk query somewhere more damaging. Gating
harm on it would make *our own judgment* the safety oracle. Auditable `notes` make the assumption
visible; they do not make it representative. The field does not exist in the bank today, so this
is a pure deletion from the plan, not a data migration.

The gate is instead the **worst case over every category in the measured vocabulary** (D0),
evaluated per row, at every swept weight — with the full (row × category × weight) matrix emitted,
not just its worst cell, and the **arg-max category named** for every row so a reader can judge
whether the damaging routing is one a real classifier would plausibly emit.

**Three harm channels**, not two. The first draft had only the first two:

| | Channel | Fires when | At-risk population |
|---|---|---|---|
| **H1** | non-KB displacement | a non-KB gold source in the baseline top-k is absent from the boosted top-k | rows with a non-KB gold source (**0 today**) |
| **H2** | refusal context injection | a `should_refuse` anchor's boosted top-k gains a KB page carrying category `c` that was **not** in the baseline top-k | the 3 refusal anchors |
| **H3** | **in-KB misrouting** | a KB gold row that baseline *hits* loses its gold source from the boosted top-k under some `c ≠ gold_category(q)` | **every gold row** (18, over 7 articles) |

H3 is new and is the **likeliest real-world harm**: a classifier assigning `Storage` to a
Running-Jobs question demotes the correct KB article out of the top-k. It costs nothing — it is
another cell of the same cached grid — and it converts every gold row into an at-risk row, which
is the only thing in this change that gives the *harm* side any observational base at all (D12).

The usual objection to a full sensitivity matrix is cost. **That objection is dissolved by D8:**
a grid cell is one in-memory stable sort over ≤20 cached candidates, with zero retrieval,
embedding, ONNX, or DB work. A |vocabulary| × |W| sweep over ~27 questions is a few thousand cheap
sorts — milliseconds. Cost is therefore not an admissible reason to narrow the gate.

**The gate is asymmetric, and deliberately so.** For a boost that adds `w` to exactly *one*
category per query under a hard-match predicate, worst-case-over-categories is a sound **upper
bound** on harm:

- **Clean worst case ⇒ a safety certificate** obtained without a classifier existing and without a
  human choosing a label. Its *generality* is bounded twice over: by the at-risk unit counts (D12)
  — a clean sweep over 3 refusal anchors certifies almost nothing about unseen refusals — and by
  the **scope condition** below.
- **Dirty worst case ⇒ HALT, not kill.** It is *not* proof the feature harms — only that harm is
  *reachable before benefit*. It halts the "proceed without a classifier" path; safety may then be
  certified only against a real candidate classifier's actual output distribution. Auto-killing on
  a category no real classifier would ever emit would replace F1's false-clear with an equally
  unprincipled false-kill, which is why the arg-max category is always named.

**The halt trigger is weight-conditioned, and it is the SAME trigger everywhere** (spec, tasks,
this file). "Some cell somewhere shows harm" is *not* a halt — that would fire on essentially every
run, which is the mirror image of the "decisive null" we just withdrew. The trigger is:

> `W_benefit` finite **and** `W_safe ≤ W_benefit` — worst-case harm becomes reachable at or below
> the first admissible weight that helps *anything*. (Or: harm reachable while `W_benefit = ∞` —
> harm and no benefit at all.)

Harm *above* `W_benefit` is the ordinary case where a usable operating window exists; it bounds the
window from above and does not halt. And harm at or above `W_LEX` (D6a) is not a harm event at all
for gating purposes.

**Scope condition — the certificate is NOT valid for the boost we will probably build.** Max-over-
*single*-categories is an upper bound only for a single-label hard-match boost. It is **not** an
upper bound for a **multi-label** boost or a **soft** boost `w · P(c | q)`: there the displaced set
is the **union** over boosted categories, and a union can strictly exceed the harm of any single
member. Both classifier candidates on the table (embedding-affinity centroids; an agent tool
argument) naturally emit a *distribution*, and `w · P(c | q)` is the obvious production form — so
this is a live risk, not a hypothetical, and pretending otherwise would be a new self-flattering
hole in exactly the place the old one was.

So the certificate ships with a **binding precondition on `decide-category-boost`**: either (a)
production boosts exactly one category per query with a hard-match predicate ⇒ the certificate
transfers; or (b) production uses a multi-label or soft boost ⇒ **it does not transfer**, and the
sweep must be re-run over category **sets** (or directly against the classifier's output
distribution). No run may state a certificate without stating which branch it assumed.

| Readout | Boost category from | Measures | Decision role |
|---|---|---|---|
| **Oracle column** | the gold article's captured category | the **ceiling** (benefit upper bound) | benefit only; authority bounded by D12 |
| **Worst-case sweep** | every measured category, worst cell | the **harm channels** H1/H2/H3 | the harm gate; clean ⇒ certificate, dirty ⇒ halt |
| ~~Authored `assumed_category`~~ | — | — | **deleted**; if ever authored, a non-normative annotation on the harm surface, never a gate |

The full (row × category) matrix is retained so that when a candidate classifier *does* exist, its
label distribution can be composed with the matrix to yield an expected-harm figure with **zero**
re-retrieval. That distribution-weighted figure supplements the worst case; it never replaces it.

### D4 — KB membership by URL prefix
A document is a KB page iff its URL is under `docs.rc.fas.harvard.edu/kb/`. This is the same
population that can carry a breadcrumb, so it exactly partitions "can ever be boosted" from
"can never be boosted" — which is what `non_kb_share@k` needs to mean.

### D5 — `refusal_confidence` must be de-confounded before it means anything
The obvious definition — "the top-k rerank score mass on `should_refuse` anchors" — is **wrong**,
and it was in the first draft. The boost *adds `w` to every matched chunk by definition*, so
"scores rise under the boost" is an **arithmetic identity**, not evidence of degraded refusal. A
metric that reports the treatment's own arithmetic back to us would fire at every `w > 0` on every
bank, forever.

Report instead two quantities that are comparable to baseline **on the same scale**:

1. the **count of documents in the boosted top-k that are KB pages carrying the boosted category
   and were absent from the baseline top-k** — i.e. context the boost *newly manufactured* for a
   question the assistant must decline (this is exactly harm channel H2); and
2. the **baseline, pre-boost rerank scores** of the documents in the boosted top-k — asking "is
   the promoted context *actually* more relevant, or did it only look that way because we added
   `w` to it?"

Read both from the **worst-case sweep** (D3a), never from an oracle run: under the oracle these
rows carry no category, are never boosted, and the metric is trivially flat — a vacuous "safe".

### D6 — Sweep `w` analytically, never sample it
The boost is added to a **FlashRank cross-encoder score**, not a cosine similarity, and that
scale is model-dependent (`ms-marco-MiniLM-L-12-v2` by default). `w` therefore cannot be
reasoned about a priori.

But it does not need to be *sampled* either. The boost is additive and monotone, so a matching
candidate `i` overtakes a non-matching `j` exactly when `w > s_j − s_i`. **The returned top-k is a
step function of `w` whose breakpoints are precisely the pairwise rerank-score gaps in the pool**
(≤ C(20,2) = 190 per query, in practice far fewer). Enumerate the gaps and you have the entire
sweep, exactly, at unlimited resolution — no grid, no risk of stepping over an interesting `w`.

### D6a — …but the sweep must STOP below the lexicographic weight, or the harm gate fires vacuously
An *exact* sweep is not the same as an *unbounded* one, and the first revision forgot the second
half. Rerank scores are bounded, so at a large enough `w` the additive boost is **lexicographic**:
every category-matched candidate outranks every unmatched one, whatever the cross-encoder thought.
In that regime a harm cell is an arithmetic certainty for essentially any pool containing ≥ k
distinct-parent chunks of some non-gold category — so "some (row, category, weight) cell shows
harm" becomes a statement about arithmetic rather than about the feature. An unbounded sweep with
an unconditional harm trigger is a gate that **always fires** — the mirror image of the "decisive
null" D12 withdrew.

The bound is **derived, not chosen**:

```
w_lex(q) = max rerank score in q's pool − min rerank score in q's pool   # lexicographic on q
W_LEX    = min over bank queries of w_lex(q)                             # lexicographic somewhere
admissible operating range: 0 < w < W_LEX
```

Weights ≥ `W_LEX` are the **degenerate regime**: the boost has stopped being a soft re-scoring and
become a hard category preference — a *different* feature, with strictly worse harm properties, and
a hard metadata filter is an explicit non-goal. They contribute to no `W_safe`, no `W_benefit`, no
window, and no verdict; they may be reported only as a diagnostic labelled "hard-filter equivalent,
out of scope". `W_LEX` and the observed score range are reported with every run, because they are
properties of *this* reranker's score scale and move if the model changes.

### D7 — The bank's blind spot is a PHASE B prerequisite, not a Phase A edit
Every gold source in every bank is a KB page — **zero** in `slurm.schedmd.com`, none on the
namesake wiki page. The set is structurally incapable of observing non-KB demotion (H1 has **n=0**
at-risk units). Closing it needs ~5 questions whose gold source is a SchedMD page **and at least
one** answered by the wiki page, so both non-KB source groups are represented (the corpus has three
groups, not two), in the modern RAGAS 0.3.5 dialect (`user_input`/`reference`/`sources`) — not the
legacy `question`/`answer` fields still described in the `retrieval-benchmarking` spec text — and
still passing `benchmark-bank-preflight`.

**This change ships none of them.** The bank additions are **Phase B** (tasks 10.3), for one
reason: everything in Phase A's arithmetic is stated against the *current* bank — H1's `n = 0`,
H2's `n = 3`, H3's `n = 7`, the ~43%/~100% rule-of-three bounds, "18 gold rows over 7 articles". If
this PR also edited the bank, every one of those numbers would be wrong on the day it landed. Phase
A therefore runs on the bank as it stands and **reports its own blindness as a finding**; Phase B
fixes it, together with the ≥30-article expansion, as the prerequisite to any decision.

Coverage alone is insufficient — and, crucially, **it is not the same problem as statistical
power** (D12). Non-KB documents can never carry a category and can never be boosted, so these
rows add **zero** benefit-side clusters. They fix harm *visibility*; they do nothing for
*decidability*. The two must not be credited to one fix.

### D8 — One retrieval per query; the whole (category × weight) grid is offline arithmetic
Everything upstream of the seam is invariant to the boost:

- `_generate_candidates` (`hierarchical_retriever.py:125-138`) takes only the query plus static
  config and passes **no** metadata filter — the pool cannot depend on `(c, w)`.
- `_rerank` (`:213-218`) hands FlashRank `{"id", "text", "meta": {}}` — it *blanks* the metadata
  channel, so the score is a pure function of the (query, passage) token pair and structurally
  cannot see `category`.
- The one DB call after the seam, `_fetch_parents` (`:174`), is a set-membership lookup
  (`WHERE p.id = ANY(%s)`) returning a **dict**, and the id list it receives is built by an
  **exhaustive** loop over all 20 candidates with no `break` (`:243-257`; truncation is a
  *later* loop at `:271-272`). A different boost permutes that list but cannot change its
  membership.

So: capture the pool once per query — `(rerank_score, category, parent_id, url)` per candidate,
plus the baseline `ranked` order and one pre-fetch of every distinct parent — and every grid
cell is a stable sort + dedupe + truncate replay over ≤20 elements. Pre-fetching all parents is
**exact**, not an approximation: it yields the identical mapping production would build at any
`(c, w)`.

Three parity hazards, all handled:
1. **Tie-break parity.** FlashRank returns a stable descending sort; Python's `sorted` is stable.
   The replay must re-sort over the identical captured baseline order. Guard: **replay at `w=0`
   must reproduce the live top-5 exactly**, or every downstream number is void.
2. **Shared-object mutation.** `:269` does `doc.metadata["rerank_score"] = score` — an in-place
   mutation of the cached `parents` dict. Cache **plain dicts, never `Document`s**, or one cell's
   score leaks into the next.
3. **Cache key.** The pool is invariant to `(c, w)` but *not* to `candidate_pool_size`,
   `semantic_weight`, `bm25_weight`, the reranker model, or the query. Key on all of them.

This is what makes the worst-case harm envelope (D3a) free, and what makes the analytic `w`-sweep
(D6) possible.

### D9 — Two prechecks that can invalidate the instrument before it is used
Both are computable from one baseline run, before a single line of boost code runs.

**Headroom.** The boost reorders only *inside* the 20-candidate pool; it cannot recall what
`hybrid_search` never fetched. So, exactly:

```
max_possible_lift(hit@5) = pool_recall@20   − baseline_hit@5
max_possible_lift(MRR)   = MRR_pool_ceiling − baseline_MRR   # ceiling = rank 1 for every in-pool gold
```

If either is 0, a Δ=0 on that metric is an artifact of the instrument, not evidence about the
feature. Report `pool_recall@20`, `baseline_hit@5`, `baseline_hit@1`, `baseline_MRR`,
`MRR_pool_ceiling`, and the full rank-of-first-gold distribution (so MRR's headroom is *measured*,
not assumed).

**`max_possible_lift` belongs to the two *inferential* metrics only.** The primary structural
readout (min-weight-to-flip) has no ceiling and is never "instrument-dead" — the first revision
re-labelled the hierarchy but left the old "primary and secondary" phrasing in the headroom text,
which made `NO_HEADROOM` literally uncomputable and told the operator to skip the one readout that
survives saturation. Corrected: **MRR headroom is the single trigger**, because it is strictly
stronger. `max_possible_lift(MRR) = 0` ⟺ every in-pool gold source is already at rank 1 ⟹
`max_possible_lift(hit@5) = 0` (rank 1 is inside top-5) *and* `W_benefit = ∞` (nothing to promote,
nothing to re-rank). So MRR-headroom-zero kills the benefit side of the **primary** readout too, and
that is what `NO_HEADROOM` means.

`NO_HEADROOM` does **not** stop the run. `w*_harm`, `W_safe`, and the harm matrix are still
computable and are the only thing here that can halt the feature — so the sweep still runs. What
`NO_HEADROOM` stops is *interpreting a benefit null*.

**Degeneracy.** A uniform additive offset is order-preserving. `hybrid_search` already selects
topically relevant chunks, so a pool dominated by the query's own category gets the boost almost
everywhere and the transform degenerates toward `s + w`, which reorders nothing. Report the pool
category-match fraction and the count of ordered pairs `(i, j)` with `i` matching, `j` not, and
`j` outranking `i` — that pair set is the *complete* set of reorderings any `w` could ever
achieve. Empty ⇒ **structurally inert**, and inert rows must be reported separately, not averaged
into a delta they can only drag toward zero.

Note also that `category` is written identically to a parent and every one of its children at
ingest (`manager.py:835-843`) — it is an **article-level** attribute. The boost adds the same
constant to every chunk of an article, so it can only reorder *across* articles, never within one.

### D10 — Metric hierarchy: min-w-to-flip primary, MRR secondary, hit@k guardrail only
`hit-rate@k` is the coarsest possible estimator and it is **saturated by design**: 10 of the 18
gold rows are `easy_retrieve` anchors, which the repo's own docs define as ceiling-pinned ("should
always score high; if it regresses, the retrieval pipeline broke"). And the power ceiling is
arithmetic, not an estimate — a paired binary hit@5 comparison is an exact sign test (p = 0.5^d for
`d` favorable discordant pairs), so p<0.05 needs `d ≥ 5`; baseline **misses** upper-bound `d`;
therefore **if baseline hit@5 ≥ 15/18, a significant benefit is mathematically impossible at any
`w`, for any feature, however good.**

`recall@k` is not the escape hatch: **no** bank row carries more than one gold source, so
`recall@k` ≡ `hit@k` here. MRR is only a marginal upgrade (6 attainable levels) and is *itself* at
ceiling if gold already ranks 1 — its headroom must be measured (D9), not assumed.

So the primary readout is **min-weight-to-flip** (D6), computed inside the admissible range (D6a).
It is continuous in the reranker's score space, has **no ceiling**, and stays informative exactly
where hit@k goes blind.

**Every `min` here gets an explicit empty-set convention.** A silent `min` over an empty set is how
a vacuous verdict gets manufactured — and the first revision had exactly that bug: `W_safe` was
given `∞` explicitly while `W_benefit` (a min over *baseline-MISS* rows) was not, so on the
hit-saturated bank this design *predicts* — zero baseline misses — `W_safe ≤ W_benefit` would hold
against any finite `W_safe` and the headline would print "the feature is dominated" **by
construction**, in precisely the regime the primary readout was invented to survive. Compounding it,
`w*_gold` was forced to 0 for already-hit rows, which deleted rank-improvement — the *only* benefit
a hit-saturated bank has — from the benefit side entirely.

Corrected, with benefit split into its two real channels:

```
w*_gold_hit(r)  = smallest admissible w promoting r's gold source INTO the top-k    # baseline-MISS rows
                  (∞ if gold not in pool, or if promotion needs w ≥ W_LEX)
w*_gold_rank(r) = smallest admissible w STRICTLY IMPROVING the rank of r's gold      # baseline-HIT rows, rank > 1
                  (∞ if gold already ranks 1, or if no admissible w improves it)
already_hit / already_first = FLAGS, never a w* of 0 — a 0 would enter a min and destroy it
w*_harm(r)      = smallest admissible w at which ANY measured category triggers H1/H2/H3 on r

W_safe        = min over ALL instrumented rows of w*_harm(r)          # +∞ if no row is ever harmed
W_benefit_hit = min over baseline-MISS gold rows of w*_gold_hit(r)    # +∞ WHEN THAT SET IS EMPTY
W_benefit_rank= min over baseline-HIT, rank>1 rows of w*_gold_rank(r) # +∞ WHEN THAT SET IS EMPTY
W_benefit     = min(W_benefit_hit, W_benefit_rank)                    # first w that helps ANYTHING
```

The domination test is then **conditioned on benefit existing at all**:

- `W_benefit` **finite** and `W_safe ≤ W_benefit` ⇒ **dominated on the rows measured**:
  `HARM_REACHABLE`, halt.
- `W_benefit` **finite** and `W_safe > W_benefit` ⇒ an **operating window** `[W_benefit, W_safe)`.
- `W_benefit = ∞` ⇒ **do not print "dominated."** A min over an empty/unreachable benefit set is
  not evidence that harm precedes benefit; it is evidence this bank has no benefit to precede.
  Report instead either `NO_HEADROOM` (no `w` of any size could help — every in-pool gold already
  ranks 1) or **"benefit reachable only in the degenerate regime"** (some `w ≥ W_LEX` would help,
  i.e. only once the boost has become a hard filter — which is out of scope, not a licence).

Two honesty constraints, both of which go in the spec:
- These are **per-row entailments over captured scores**, not population estimates. Report them
  **without** confidence intervals, and say plainly that they characterize the rows measured.
- `W_safe` is a **min over instrumented rows**, so adding at-risk rows can only *lower* it. It is
  an **optimistic** ceiling, never a conservative one — the opposite of how a safety number is
  usually read, so it must be labelled.

### D11 — Uncertainty is cluster-level or it is fiction
The gold **article** is the resampling unit. Questions pointing at the same article are
near-duplicates: whether the article lands in top-5 is a property of *that article's* chunk mass,
shared by all of them, so ICC ≈ 1 by construction (D9's article-level `category` proves it).

The bank: 18 gold rows over **7 articles**, sizes `[5,5,2,2,2,1,1]`; `running-jobs` and
`cluster-storage` hold 56% between them. Design effect `1 + (m̄−1)·ICC` with `m̄ = 2.57` ⇒ **ESS ≈ 7,
not 18** — and ≈ **5** at the *treatment* unit (the category), which is what the boost is actually
applied to.

Mechanics: paired per-row delta, resampled over articles, aggregated with the **ratio estimator**
(Σ per-article delta sums / Σ per-article row counts). *Not* mean-of-per-article-means — clusters
are badly unequal and mean-of-means silently reweights a 1-question article equal to a 5-question
one, changing the estimand.

Zero new deps: `numpy==1.26.4`, `scipy==1.13.1`, `pandas==2.3.2` are already direct pins
(`requirements-base.txt:83,64,68`). Either `scipy.stats.bootstrap(..., paired=True,
vectorized=True)` over `(cluster_sum, cluster_n)` — the statistic **must** take an `axis` kwarg or
scipy raises `TypeError` — or a ~12-line numpy resample loop. `statsmodels` is **not installed**
(would be a new dep); `sklearn` imports but is **transitive-only** and pinned nowhere — do not
import it. Nothing in-repo to reuse (`build_leaderboard` emits bare means).

### D12 — Harm is a MIN, benefit is a MEAN; decision authority is bounded by evidence class
**This is the load-bearing decision of the redesign, and the asymmetry is the whole idea.**

- A **harm** claim needs *one witness*. A single (row, category, weight) cell in which the
  treatment displaces a correct document is an **entailment** — it requires no statistical power
  to observe, and it stands at K=7.
- A **benefit** claim needs a *population average*. 18 gold rows over 7 articles (two holding 56%)
  cannot supply one, in either direction.

The first draft's asymmetry — *a null is decisive, a positive is provisional* — is **withdrawn**,
and it was backwards twice over. At K=7:

- A nominal 95% cluster-bootstrap interval delivers materially **less** than 95% actual coverage,
  and the shortfall persists past K=20. Coverage is the *precondition* for power, so you cannot
  rescue the gate by bolting a CI onto this bank — the interval would lie about its own confidence.
- A paired sign test over 7 clusters clears p<0.05 **only on a 7/7 unanimous result** (6/7 ⇒
  p=0.125). One article moving the wrong way makes significance unreachable at *any* effect size.
- MDE is catastrophic: a plausible hit-rate delta lands far below the conventional 80% power floor.

**Those coverage/power numbers must be COMPUTED, not asserted.** The whole ≥30-article
prerequisite — the thing that blocks the decision — rests on them, and asserting a constant in
normative text with nothing to derive it is the same class of error as authoring `assumed_category`:
a number nobody can check, doing load-bearing work. `numpy` is already pinned, so a fixed-seed
Monte-Carlo costs **zero** dependencies. Ship one (task 8.7): actual coverage of the nominal-95%
article-level cluster bootstrap at K = 7/12/20/30 on the real cluster-size vector, and power at a
stated effect size, with **seed, cluster sizes, assumed ICC, base rate, and replicate count
published alongside** so a reader can re-run and disagree.

The working figures (**~83 / ~86 / ~92 / ~94%** coverage at K = 7/12/20/30; **~23%** power at
+14pp) are **provisional pending that simulation** and are to be replaced by its output; the ≥30
minimum is then **re-derived** from it. If the simulation contradicts them, the minimum moves and
the spec is corrected — the minimum may move **only** by a published seeded re-derivation, never by
judgment. The closed-form pieces (sign test 7/7 ⇒ p=0.0156, 6/7 ⇒ p=0.125; rule of three 3/n; "a
significant benefit is impossible once baseline hit@5 ≥ 15/18") are exact arithmetic and need no
simulation.

A "decisive null" on this instrument is **unfalsifiable**: between saturation (D10) and the pool
ceiling (D9), the null is the only outcome the bank can produce. And a *positive* at K=7 is equally
inadmissible. So findings get graded, and each grade gets only the authority it can carry:

| Class | What it is | Decision authority |
|---|---|---|
| **Corpus-deductive** | property of the corpus, bank-independent (category coverage + measured vocabulary, D0) | MAY halt the work at any K — but as a verdict on the **extractor**, not the **idea** |
| **Bank-structural** | exact property of the captured pools (zero headroom, inertness, a witnessed harm cell, `W_safe ≤ W_benefit`) | no sampling error, but generalizes only as far as the bank ⇒ **hypotheses** below the coverage minimums — *except* a harm cell **firing the halt trigger** (`W_benefit` finite and `W_safe ≤ W_benefit`, or harm with `W_benefit = ∞`), which always halts the no-classifier path; config-scoped kills at or above the minimums |
| **Inferential** | Δhit@k, ΔMRR, intervals | **none, in either direction**, below the coverage minimums |

Note what is *not* in that table: a harm cell **above** `W_benefit` (it bounds the window, it does
not halt) and a harm cell in the **degenerate regime** `w ≥ W_LEX` (not a harm event at all for
gating — D6a).

**Coverage minimums** (before any CI- or significance-based rule may be applied): **≥30 distinct
gold KB articles**, **≥6 distinct captured categories**, **no article >10% of gold rows**
(today: 28% each for two of them), and **≥12 independent at-risk units per harm channel**.

**The harm gate is powered too, not merely unbiased.** A worst-case sweep is exact *over the rows
it covers*, but "the treatment is harm-clean" is a claim about *unseen* queries — a sampling claim,
subject to the same discipline as the benefit claim. With 0 harmful units out of `n`, the 95% upper
bound on the true harm rate is ≈ `3/n` (rule of three). The denominator is the number of
**independent** at-risk units, and — applying D11's own logic — that unit differs by channel:

| Channel | Independent unit | `n` today | Rule-of-three bound |
|---|---|---|---|
| **H1** non-KB displacement | distinct non-KB gold page | **0** | undefined — entirely unobserved |
| **H2** refusal injection | `should_refuse` anchor | **3** | ≈ **100%** — establishes nothing |
| **H3** in-KB misrouting | gold **article** (not row — the boost moves an article's rows together) | **7** | ≈ **43%** — the strongest harm evidence available, and still not "clean" |

`n ≥ 12` ⇒ ≤25% (the floor for saying "harm-clean" at all); `n ≥ 30` ⇒ ≤10% (required before
production enablement). Note that H3 is the *only* channel with any observational base today, and
it exists only because we added it — which is the strongest argument for having done so.

**The verdict field.** Every result record carries `verdict`, defaulting to **`INDETERMINATE`**,
and a benefit delta — positive *or* null — cannot move it. Only non-inferential conditions can:

| Verdict | Trigger (all pre-registered, none an operator judgment) |
|---|---|
| `CORPUS_BLOCKED` | `coverage_KB < 0.50` or `\|V\| < 2` (D0). Bank-independent ⇒ stands at any K. |
| `NO_HEADROOM` | `max_possible_lift(MRR) = 0` ⟺ every in-pool gold already ranks 1 ⟹ hit@5 headroom 0 **and** `W_benefit = ∞` (D9). Analytic, config-scoped. Below the minimums it is a hypothesis about *this bank*, not a kill; at/above the minimums, reproduced, it MAY kill. It never suppresses the harm sweep. |
| `HARM_REACHABLE` | `W_benefit` finite and `W_safe ≤ W_benefit`, or harm reachable with `W_benefit = ∞` — at admissible weights only (D6a). **Halts** the no-classifier path; not a kill. |

If several hold, all are recorded and `HARM_REACHABLE` is the operative one — it is the only
verdict that halts a *path* rather than describing an *instrument*. **Absence of demonstrated harm
is never recorded as safety**: the instrument can witness harm; it cannot clear it (and even a clean
sweep is conditional on the single-label scope condition in D3a).

**Consequence, stated plainly: the current bank supports no kill and no advance decision.** Bank
expansion is a **prerequisite** of the decision, not a follow-up to it. Phase A builds the
instrument and reports diagnostics; Phase B expands the bank and only then decides. To keep the
deferral bounded rather than open-ended, the write-up **names the successor change**
(`decide-category-boost`) and **publishes its entry condition** (the coverage minimums above). If
expansion is not funded, the honest close-out is *"harness built, feature undecided"* — never
*"feature killed"*.

## Risks / Trade-offs

- **The bank is not merely thin — it is below the threshold at which its own statistics work.**
  18 gold rows over 7 articles (two holding 56%), ESS ≈ 7 at the article level and ≈ 5 at the
  treatment level. This is **not** mitigated by careful reporting: at K=7 the "95%" interval has
  ~83% coverage and a significant benefit is unreachable unless every article moves the right way
  (D12). The mitigation is Phase B — expand the bank *before* deciding — and the accepted cost is
  that this change ships an instrument and a diagnosis, not a verdict.
- **Phase A may look like a wasted run.** It is not: it produces the parity-verified capture
  harness, the headroom and degeneracy prechecks, and the `w*` surface — all reusable verbatim on
  the expanded bank, and any of them may cheaply falsify the feature's *mechanism* before the
  expensive bank work is funded. But it must not be written up as a verdict, and the temptation to
  do so is the single biggest risk in this change.
- **Coverage confound (D0).** Mitigated by making category coverage a hard precondition — and it
  is the *one* corpus-level, bank-independent finding here, so it is the only thing that can halt
  the work at K=7 (and even then, as a verdict on the extractor rather than on the idea).
- **The oracle is optimistic by construction.** It resolves every ambiguous query to the "right"
  category by definition. That is intended — it is an upper bound — but it means the real
  classifier lands *below* whatever the sweep shows, never above. It also means the oracle
  **cannot show harm at all** (D3a), which is why harm is read from the worst-case envelope.
- **The worst-case sweep is conservative on the CATEGORY axis and optimistic on the ROW axis.**
  Conservative: it reports the most damaging category for each row, which no single real classifier
  would pick for every row at once, so it *overstates* harm relative to a deployed classifier. That
  asymmetry is deliberate — an overstated harm bound costs us a feature, an understated one costs a
  user a fabricated answer. But optimistic in the other direction: `W_safe` is a **min over the
  rows we instrumented**, so a harm mode that only fires on a query class *absent from the bank* is
  invisible, and adding rows can only lower `W_safe`. The sweep is worst-case over **categories**,
  not over **queries**. Both directions must be stated; reporting only the conservative one would
  be its own form of dishonesty.
- **A dirty worst case could be a false kill.** Maximising over the whole vocabulary will plausibly
  find *some* category that harms *some* row at *some* weight — that is nearly what "adversarial
  upper bound" means. If it fires on a routing no real classifier would ever emit, we have replaced
  F1's false-clear with a false-kill. Mitigations: the verdict is **`HARM_REACHABLE` (halt)**, not a
  kill; the **arg-max category is always named** so a human can see whether the routing is absurd;
  and the trigger is narrowed to weights at or below the weight benefit first appears. The
  underlying problem — that the *plausibility* of a routing is exactly the thing we refused to
  author — is not solved here, and only a real candidate classifier's error distribution can settle
  it. That is deliberately out of scope.
- **A clean worst-case sweep proves little on the current bank, and the bound differs per channel.**
  Rule of three over *independent* units: H1 has **0** (no non-KB gold rows exist — entirely
  unobserved), H2 has **3** refusal anchors (⇒ ~100% bound — establishes nothing), H3 has **7**
  gold articles (⇒ ~43%). Only H3 has any base at all, and it exists only because we added the
  channel. ≥12 units per channel before "harm-clean" may be said at all.
- **The harm channels are ours.** A channel we did not define is not measured — e.g. the boost
  degrading answer *quality* while preserving top-k membership, or reordering *within* the top-k in
  a way that shifts what the LLM attends to. The benchmark is retrieval-only by design and builds
  no answer LLM, so it cannot see either. Everything here is also scoped to one retrieval
  configuration (`candidate_pool_size=20`, `k=5`, the current hybrid weights, the FlashRank
  `ms-marco-MiniLM-L-12-v2` scale); harm and headroom at other settings are unmeasured, which is
  why a kill must name its config.
- **We may build an instrument for a feature nobody funds the bank to decide.** Accepted. The
  alternative — deciding on K=7 — is worse, because it produces a confident answer with roughly
  the reliability of a coin.
- **Requires a nuke + full re-ingest of dev.** A plain re-ingest will **not** refresh #97's
  sliced bodies: `_handle_standard_url` calls `persist_resource` without `overwrite`, and
  persistence skips files that already exist. The dev deploy-verify already hit this.
- **The identity hook is production code.** It is behavior-neutral, but it is still a diff on
  the live retrieval path and must carry a test proving the base implementation is identity. It is
  **not** consumer-less: after D8 its consumer is the benchmark's **capture** override (D1), which
  records `candidates`/`ranked`/scores and returns `ranked` unchanged. If that framing ever stops
  being true — if the benchmark starts capturing somewhere else — the hook must be removed, not
  left as a boost seam nothing boosts through.
- **The safety certificate is narrower than it sounds.** It is an upper bound only for a
  single-label hard-match boost. The production form we will most likely want (`w · P(c | q)` over a
  classifier's distribution) makes the displaced set a **union** over categories, which can exceed
  any single category's harm — so the certificate does not transfer, and `decide-category-boost`
  must re-sweep over category *sets*. Stated as a binding precondition (D3a) rather than buried.
- **Measuring against a live shared deployment.** The scorer is read-only, but it runs against
  the dev corpus; runs should be coordinated so a concurrent re-ingest doesn't shift the corpus
  mid-sweep.
