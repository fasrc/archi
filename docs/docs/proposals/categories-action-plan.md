# Categories: One Vocabulary, One Measurement, One Decision

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** September 2026
**Status:** Proposal — for review
**Baseline read:** `origin/dev` @ `4b253e26`, 2026-09-19; `fasrc/archi-config` `main` @ `56e4bac3`
**Companion:** [Release Plan 2026](release-plan-2026.md) · [ICL and Query→Category Mapping](icl-and-query-category-mapping.md) (PR [#511](https://github.com/fasrc/archi/pull/511), merged 2026-09-21) · [Feature-Matrix Campaign](feature-matrix-campaign-2026.md) · [Multi-Collection Routing](multi-collection-routing.md)

Every path below is relative to the `fasrc/archi` repository root unless it is marked
**archi-config**, in which case it is relative to the `fasrc/archi-config` root (checked
out at `config/`).

---

## TL;DR

archi carries **at least five different notions of "category"** and consumes none of them
on the answer path by default. Two are written at ingest into every FASRC document. One is
proposed for the query side. Two more label the questions in the evaluation banks. They
share no vocabulary, no reader, and no report.

The cost of this is measured. The LLM label costs **19.2 minutes per ingest** on the FASRC
host and moved no retrieval metric outside noise
([#496](https://github.com/fasrc/archi/issues/496), campaign arm 03). The breadcrumb
label is free and covers **96.7 % of documentation articles**, and no code reads it. A
category boost was designed and **shelved on 2026-07-13** with five recorded objections
(`openspec/changes/measure-category-boost-ceiling/proposal.md`). Two prompt arms that
can test the query side are filled and unrun
([archi-config #22](https://github.com/fasrc/archi-config/pull/22)).

This plan asks for three things, in this order:

1. **One decision now.** Turn the LLM writer off on FASRC (the disposition
   [#496](https://github.com/fasrc/archi/issues/496) already asks for) and keep the free
   breadcrumb writer. Adopt the breadcrumb vocabulary as the **only** document category
   vocabulary in this fork.
2. **One measurement.** Run the rung-0 prompt sweep under the campaign's own verdict
   machinery, tracked by one `evidence-trial` issue, and read only the count-type metrics.
3. **One gate.** No category code enters a milestone without a verdict from step 2.
   If step 2 shows nothing, the question closes and categories stay a free ingest field.

The plan also adds the one evaluation piece nobody has built: a **per-category slice** of
the golden set with recorded provenance, so a future report can say which documentation
sections got better and prove which corpus it read.

---

## 1. What we know

Every row below is a measured or recorded fact with its source. Nothing in this section
is an opinion.

| Fact | Value | Source |
|---|---|---|
| LLM categorization cost per ingest | 3 802 s vs 4 956 s = **−19.2 min (−23 %)** on 1 091 documents. The two ingests were not corpus-identical (30 chunks apart, 0.43 %), so read this as ≈ −23 %; the mechanism is one LLM call per document (`src/data_manager/collectors/processing.py:881`, serialized at `:868`) and the per-file row below corroborates it | [#496](https://github.com/fasrc/archi/issues/496); `bench_out/feature_matrix/ledger.json` arms 00 and 03 |
| Retrieval effect of the LLM label | **No resolvable effect, and not a clean isolation.** `context_precision` −0.004 / −0.002 against an MDE of 0.025 / 0.027; source accuracy 0.868 / 0.840 vs 0.868 (McNemar p = 1 / 0.38). Every delta sits inside noise, **and** the two arms' corpora differed (row below), so this reads as "no effect measurable at this power, on corpora that were not identical" — not as evidence that the label cannot help | [#496](https://github.com/fasrc/archi/issues/496) |
| Chunk count, arm 00 vs arm 03 | 6 926 vs 6 896 — the two arms **ingested different corpora**. The toggle cannot cause this: `CategorizationProcessor.process` writes only `metadata["llm_category"]` (`src/data_manager/collectors/processing.py:855-858`) and `CORPUS_STATE_QUERY` hashes document size and chunk and parent text, never that key (`src/bin/service_benchmark.py:106-129`). The 30-chunk delta is corpus drift between the two ingests, so **the arms did not isolate categorization** | ledger arms 00 and 03 |
| Second cost of the LLM label | embedding ran 6.0–6.7 s per file vs 3.2 s idle because the same config ran categorization | [#378](https://github.com/fasrc/archi/issues/378) |
| Breadcrumb coverage | whole corpus 206 / 841 = 24.5 %; **KB articles 206 / 213 = 96.7 %**; non-KB documents 0 / 628 | archi-config #22 `benchmarking/prompt_sweep_r0/README.md` §1, claw Postgres 2026-09-19 |
| Breadcrumb vocabulary | **19** Title Case labels (`Software`, `Cluster Usage`, `Storage`, …) | same |
| LLM vocabulary | **6** lowercase labels (`job-scheduling`, `storage`, `account-access`, `software`, `compute`, `data-transfer`) | archi-config `environments/dev.yaml:220-226` (`main`; `:101-106` at the pin — the six values under `categories:` at `:100`), `benchmarking/ragas.yaml`, all eight `benchmarking/feature_matrix/*.yaml`; host-local `deploy/fasrc-dev/config.yaml:163-169` |
| Overlap between the two vocabularies | **none** — the two share no values | archi-config #22 `benchmarking/prompt_sweep_r0/README.md` §1 |
| LLM label validity window | every document was `uncategorized` until PR [#44](https://github.com/fasrc/archi/pull/44) (2026-06-26) and again until PR [#218](https://github.com/fasrc/archi/pull/218) (2026-08-07) | those PRs |
| Prompt cost of the rung-0 arms | control ~556 tokens; r0a **+523 tokens per turn**; r0b **+485 tokens per turn** | archi-config #22 `benchmarking/prompt_sweep_r0/README.md` §4 |
| Recursion-limit blowouts today | 36 of 436 = **8.3 %** of FASRC questions, mean 232 s each | [#499](https://github.com/fasrc/archi/issues/499) |
| RAGAS resolution on this bank | MDE ≈ 0.025–0.05 per metric; a mean delta inside it needs **~40 runs per arm** | [#396](https://github.com/fasrc/archi/issues/396) comment 2026-09-04 |
| Count-type baselines | source accuracy 0.868; item pass 0.424 ± 0.024; atom score 0.475 ± 0.017; blowouts 7 / 109; 48.2 s per question | [#496](https://github.com/fasrc/archi/issues/496), campaign §4 |
| Golden-set bank shape | 105 rows; `anchor_type` = reasoning 73, easy_retrieve 29, should_refuse 3; **41 distinct KB URLs**; **no docs-category field** | archi-config `benchmarking/fasrc_ragas_queries.json` |
| Bank power minimum set in July | ≥ 30 distinct gold KB articles before any retrieval treatment is adopted or rejected on this bank; the 18-row bank of July had 7 | `openspec/changes/measure-category-boost-ceiling/proposal.md` |
| Prior decision on a category boost | **shelved 2026-07-13**, five strikes; "If the boost is ever revisited, run it as a RAGAS A/B arm" | same; PRs [#102](https://github.com/fasrc/archi/pull/102), [#103](https://github.com/fasrc/archi/pull/103) |
| Provenance | both ingest processors are **fork-only**; upstream `archi-physics/archi` has neither | PRs [#38](https://github.com/fasrc/archi/pull/38), [#97](https://github.com/fasrc/archi/pull/97); upstream code search, 0 hits |
| Upstream direction | upstream [#570](https://github.com/archi-physics/archi/issues/570) proposes a `category` metadata field set from the **input-list name**, filterable by `search_local_files` | upstream tracker |
| Campaign stacks | the `fm-00` and `fm-03` stacks no longer run on the claw workstation; only `postgres-claw` (841 documents) does | `docker ps`, 2026-09-19 |

Two claims in the record are **not** facts and this plan corrects them:

- **"The label is write-only. Readers: none."** ([#496](https://github.com/fasrc/archi/issues/496);
  the first draft of the [ICL proposal](icl-and-query-category-mapping.md) repeated it and
  corrected it in review round 1, so the merged text already names the reader).
  `CatalogPostgres.search_metadata`
  matches any unknown key by substring over `extra_text`
  (`src/data_manager/collectors/utils/catalog_postgres.py:474-481`), `_build_extra_text`
  writes both `category:<value>` and `llm_category:<value>` into that blob (`:1560-1569`),
  and the campaign prompt exposes the tool that calls it (`search_metadata_index`). A
  reader exists. It is a reader the model **can** call, and nothing tells the model the
  vocabulary. Arm 03 is evidence that the label did not help **as wired and as prompted**.
  It is not evidence that no reader was reachable. The Codex review on PR #511 made this
  point first.
- **"The 20 LLM labels."** [#496](https://github.com/fasrc/archi/issues/496) counts 20
  labels at archi-config `environments/dev.yaml:292-301`, a range that does not exist — the
  file is 116 lines at the pin. The first draft of the
  [ICL proposal](icl-and-query-category-mapping.md) repeated the count and corrected it in
  review round 1. `main` and the pinned checkout both carry **6** labels
  (`environments/dev.yaml:220-226` on `main`, `:101-106` at the pin). The 19-item list is
  the breadcrumb vocabulary. The two lists were conflated. Pin provenance: these line
  numbers were read at `deploy-pin-2026-08b`, the pin in force when this section was
  measured; `deploy-pin-2026-09b` has been the deploy pin since
  [#522](https://github.com/fasrc/archi/pull/522) and was not re-read for this row.

---

## 2. What is implemented

| # | Mechanism | Writer | Readers | Default | Vocabulary | Cost | State |
|---|---|---|---|---|---|---|---|
| A | Breadcrumb → `metadata["category"]` | `HtmlCategoryProcessor`, `src/data_manager/collectors/processing.py:181-208`; the Indico scraper writes its own event category into the same key (`src/data_manager/collectors/scrapers/integrations/indico_scraper.py:987`, `:1056`) and the processor never overwrites a source value (`src/data_manager/collectors/processing.py:202-206`) | reaches every chunk's metadata (`src/data_manager/vectorstore/manager.py:575`, `:857`); read only by the catalog substring match (`src/data_manager/collectors/utils/catalog_postgres.py:474-481`), which `parse_metadata_query` lets any `key:value` reach because it has **no key allowlist** (`src/utils/catalog_query.py:42-74`); no retriever, no prompt header, no schema tool | **on** with `html_to_markdown.enabled` (`src/data_manager/collectors/processing.py:1071-1075`) | 19 Title Case labels from the Echo-KB breadcrumb on FASRC; Indico's own categories on Indico sources | ~0 — a regex on a page already fetched | live, unread by design; reachable by accident |
| B | LLM label → `metadata["llm_category"]` | `CategorizationProcessor`, `src/data_manager/collectors/processing.py:790-857` | same substring path | **off** in code (`src/cli/templates/base-config.yaml:501-510`); **on** in every FASRC config | 6 lowercase labels | 19.2 min per ingest + slower embedding | live on FASRC, unread, disposition open ([#496](https://github.com/fasrc/archi/issues/496)) |
| C | Retrieval `filter` plumbing | `PostgresVectorStore.similarity_search` and `hybrid_search` accept a `filter` dict (`src/data_manager/vectorstore/postgres_vectorstore.py:337-352`, `:449-483`) | **nobody passes one.** `LlamaIndexHierarchicalRetriever._candidates` calls `hybrid_search(query, k, weights)` (`src/data_manager/vectorstore/retrievers/hierarchical_retriever.py:133-138`) and is the default path (`src/data_manager/vectorstore/retrievers/factory.py:54-56`); `HybridRetriever` runs only with rerank off | — | any metadata key | — | dormant. **The key is f-string interpolated into SQL** (`c.metadata->>'{key}' = %s`, `src/data_manager/vectorstore/postgres_vectorstore.py:351`, `:482`); a model-chosen key is an injection surface |
| D | Model-visible category | — | `_format_documents_for_llm` header shows title, url, hash, score, **no category** (`src/archi/pipelines/agents/tools/retriever.py:67-98`); `list_metadata_schema` formats only `keys`, `source_type`, `suffix` (`src/archi/pipelines/agents/tools/local_files.py:469-476`); `get_distinct_metadata` allows five keys, not `category` (`src/data_manager/collectors/utils/catalog_postgres.py:554-564`) | — | — | — | absent |
| E | Query → category routing | none in code | — | — | 19 breadcrumb labels, hard-coded in the prompt | +523 tokens per turn | prompt arm **r0a** filled, unrun (archi-config #22 `benchmarking/prompt_sweep_r0/fasrc-docs-r0a-category.md`) |
| F | In-context learning | none in code; no exemplar store | — | — | — | +485 tokens per turn | prompt arm **r0b** filled, unrun (archi-config #22 `benchmarking/prompt_sweep_r0/fasrc-docs-r0b-icl.md`); dynamic ICL not designed |
| G | Question-side labels | bank `anchor_type` (reasoning / easy_retrieve / should_refuse); QA dataset v2 optional `category` field (`docs/docs/cli_reference.md:273`) | `scripts/benchmarking/compare_runs.py` slices by `anchor_type` and `difficulty` (`SLICE_FIELDS`, `:85`); the QA catalog collects distinct `category` values (`src/evaluation/qa/catalog.py:537-538`), and the FASRC bank sets none; the QA dataset's `answer_mode` is a closed 4-value set; Argilla carries an 8-label `failure_modes` set (`src/utils/benchmark_argilla.py:410`) that no report aggregates; nothing joins any of these to a document category | — | disjoint from A and B | — | live, not joinable |
| H | `collection` | `PostgresVectorStore` tags every chunk (`src/data_manager/vectorstore/postgres_vectorstore.py:185`) | every query filters on it (`:345`, `:477`, `:649`) | on | one value per deployment | — | live; the same predicate as C at a coarser grain ([Multi-Collection Routing](multi-collection-routing.md)) |
| I | Upstream list-name `category` | proposed upstream ([#570](https://github.com/archi-physics/archi/issues/570)) | proposed `search_local_files` filter | — | input-list names | — | not in this fork; a **fourth** vocabulary if ported blind |
| J | `header_path` markdown header hierarchy | `src/data_manager/vectorstore/node_parsing.py:540` | **none** outside that module | only under `chunking.strategy: markdown`; the live default is `sentence` | per-document headings | — | write-only, dormant |

**Tests.** Both processors are tested (`tests/unit/test_html_category_processor.py`, 8 tests;
`tests/unit/test_categorization_processor.py`, 26 tests). The two things the rung-0 arm
r0a depends on are **not**: no test names `_build_extra_text`, and no test exercises the
`search_metadata` substring fallback. The `search_metadata_index` tool description calls
its filters "exact matches" (`src/archi/pipelines/agents/tools/local_files.py:373-381`),
which is false for any key outside `_METADATA_COLUMN_MAP`.

The per-turn seam that any dynamic variant will use already exists and is proven:
`_inject_forced_retrieval` (`src/archi/pipelines/agents/base_react.py:1752-1762`, overridden
at `src/archi/pipelines/agents/fasrc_docs_agent.py:237-302`) rewrites the message list
before the model's first turn. Its flag `force_initial_retrieval` defaults **on**
(`src/archi/pipelines/agents/fasrc_docs_agent.py:256`). Two consequences follow. The first
search of every turn is issued by the harness with the raw user question, so a prompt-only
routing arm **cannot touch the first retrieval**. And the flag is not an off-by-default
precedent, so a rung-1 or rung-2 toggle needs its own dedicated default-off key.

---

## 3. Where we double up

The concern that prompted this plan is correct. The duplication is in four places.

**Two ingest writers, two vocabularies, zero shared values.** Rows A and B label the same
documents for the same purpose. A prompt that hard-codes the 6-label list routes against
values that do not exist in `metadata["category"]`. A prompt that hard-codes the 19-label
list cannot use `llm_category`. While both writers are on, a substring filter
`category:storage` also matches `llm_category:storage`, so the two contaminate each other's
reads (archi-config #22 `benchmarking/prompt_sweep_r0/README.md` §1). That filter works
only because `parse_metadata_query` accepts any key (`src/utils/catalog_query.py:42-74`)
and the catalog falls back to a substring match for keys it does not know. Nothing
documents this path, the tool description calls its filters exact, and no test pins it.

**Three question taxonomies that do not join.** `anchor_type`, the QA dataset's optional
`category`, and the bank's gold-source URLs each classify questions. None of them connects
a question to the documentation section its answer lives in. As a result no report today can
say "Storage questions improved" or "Cluster Usage questions regressed."

**Four filter keys on one SQL predicate.** `collection` (H), `category` (A), `llm_category`
(B), and upstream's list-name `category` (I) are all `c.metadata->>'<key>' = %s`. They differ
in grain and in who sets them, not in mechanism. If the four grow apart, we keep four
vocabularies in sync and teach four prompts.

**Two measurement rigs proposed for one question.** July 2026 designed a retrieval-only
benchmark to adjudicate the category boost, then dropped it: "The RAGAS benchmark already
exists to decide whether an idea works" (`openspec/changes/measure-category-boost-ceiling/proposal.md`).
September 2026 built the prompt-sweep harness and the campaign's
`scripts/benchmarking/compare_runs.py`. The second rig is the right one and this plan
uses only it.

### The rule this plan adopts

> **One document vocabulary.** `metadata["category"]` holds the **source's own published
> taxonomy** and nothing else: the Echo-KB breadcrumb for the documentation site (19 labels
> as measured on the corpus), Indico's event category for Indico sources. archi does not
> invent document labels. A new taxonomy enters only if it maps onto the source's or is a
> different grain (`collection`) with a stated purpose. No prompt, config, or report
> carries a label set the source does not publish.

This rule retires row B, decides row I in advance, and gives rows E, G, and the evaluation
work in §6 a common key.

---

## 4. Decisions a human must make

The plan cannot proceed on the items below without a recorded decision. Each has a
recommendation.

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | Turn `processing.categorization.enabled` **off** in archi-config `environments/dev.yaml`, archi-config `benchmarking/ragas.yaml`, and the host-local `deploy/fasrc-dev/config.yaml`; redeploy | **Yes** | It is the campaign's own disposition ([§4.4 rule 5](feature-matrix-campaign-2026.md#44-the-verdict-rule)); cost measured, effect nil; removes the substring contamination in §3 |
| D2 | Keep `HtmlCategoryProcessor` default-on | **Yes** | It is free, covers 96.7 % of KB articles, and is the vocabulary of record |
| D3 | File **one** tracking issue for the rung-0 sweep and label it `evidence-trial` **before** the sweep runs | **Yes** | The release plan's invariant requires every open issue to carry a milestone, `parked`, or `evidence-trial`. AGENTS.md defines `evidence-trial` as operator-driven evidence work. The ICL proposal's "do not file yet" leaves the sweep invisible to every report. The Codex review on PR #511 raised this as P1 |
| D4 | Order D1 relative to the sweep | **D1 first**, then lock the sweep | D1 requires a re-ingest, and any re-ingest re-scrapes, so the corpus fingerprint moves. The arm-00 against arm-03 pair is the evidence: two ingests of the same site came out 30 chunks apart (§1), which the toggle cannot cause. A sweep locked before D1 therefore measures a corpus production will no longer run |
| D5 | Where [#496](https://github.com/fasrc/archi/issues/496) sits | **`v2026.10.0`**, or `parked` if the operator declines D1 | Today it carries neither a milestone nor `parked` and breaks the invariant. It is the campaign's output and the release claims measured defaults |
| D6 | Archive `openspec/changes/measure-category-boost-ceiling/` as shelved | **Yes** | It sits among active changes with a "do not implement" banner; archive it so the record is findable and does not read as open work |
| D7 | Whether to port upstream [#570](https://github.com/archi-physics/archi/issues/570) when it lands | **Only if** it maps its values onto the breadcrumb vocabulary or is scoped to `collection` | The rule in §3 |

Rungs 1 and 2 (code) are **not** decisions for today. They are gated on the verdict from §5
Phase 2.

---

## 5. Path forward

Each phase has an exit condition. No phase starts before the one above it exits.

### Phase 0 — Fix the record (this week, no measurement)

1. Amend PR [#511](https://github.com/fasrc/archi/pull/511) for the Codex findings that
   hold (Appendix A lists each with a verdict). React to every comment.
2. Update the body of archi-config PR [#22](https://github.com/fasrc/archi-config/pull/22):
   both arms are filled; the token deltas are +523 and +485, not +388 and +352.
3. Apply D6: archive the shelved change.
4. Apply D1 as an archi-config PR plus the archi template-comment and docs change
   [#496](https://github.com/fasrc/archi/issues/496) specifies. Redeploy. Record the
   acceptance number: ingest at least 15 minutes faster than the 82.7-minute baseline.
5. Apply D3: file the tracking issue. Its body is the pre-registration in Phase 1.

**Exit:** D1 deployed and its ingest time recorded; the `evidence-trial` issue open; PR #22
merged into archi-config `main`.

### Phase 1 — Rung 0: the prompt-only sweep

Three prompt files through `scripts/benchmarking/generate_prompt_sweep.py`, from the
manifest already on archi-config (`benchmarking/prompt_sweep_r0/prompt_sweep.yaml`): the
locked control, **r0a** (category routing), **r0b** (static ICL). No code change. Two runs
per arm plus the control, on one stack, one corpus fingerprint, categorization **off** (D4).
The second run needs a benchmark-only rerun, and the sweep has no such path yet. A second
`archi evaluate` refuses unless forced (`src/cli/utils/helpers.py:310-313`), and `--force`
keeps the data volumes (`helpers.py:343-348`, `remove_volumes=False`) but recreates the
containers and re-ingests, which moves the corpus. `run_arm.sh --rerun` already recreates only
the benchmark container, between two corpus-pin checks
(`scripts/benchmarking/feature_matrix/run_arm.sh:52-53`, `:61`, `:65`) — but it is gated to
feature-matrix arm labels and an active campaign lock (`lib.sh:29`, `run_arm.sh:57-61`) and
never rewrites `agent_md_file`, so W6 must extend that path rather than invent one.

The sweep reuses the campaign's verdict machinery
([§4.4](feature-matrix-campaign-2026.md#44-the-verdict-rule) and
[§7](feature-matrix-campaign-2026.md#7-invariants-the-compare-step-enforces)) with one
change: the primary metrics are count-type, because RAGAS means cannot resolve a prompt
effect at two runs. The pre-registration, corrected from the ICL proposal:

| Item | Value |
|---|---|
| Bank power | 105 rows over **41 distinct gold KB articles** — clears the **≥ 30 distinct gold KB articles** minimum the July record set for an adopt-or-reject decision on this bank (`openspec/changes/measure-category-boost-ceiling/proposal.md:199-201`). That record sets two further benefit-side prerequisites that **do** bind a category-conditioned arm: **≥ 6 categories** and **no article > 10 % of gold rows**. Both are read by the bank-coverage census (§6.2), which runs as a pass/fail gate before any verdict is read — a bank below either minimum **voids** the arm rather than downgrading it. The record's remaining two prerequisites, **≥ 12 at-risk rows** (`:200-201`) and **non-KB gold source coverage** (`:196-198`), are harm-gate items: they exist to make a *retrieval boost's* harm cells visible, and r0a boosts nothing, so they do not transfer to a prompt-only arm, whose harm side is the G8 guard and the blowout count. They return as prerequisites if rung 1 or rung 2 is ever measured. A per-category claim has its own minimum (§6.1) |
| Void checks | the campaign's §7 invariants: corpus fingerprint equal across arms and runs; scored counts equal; control sha256 `ac22702a…4ce8` unchanged; `grep FILL_FROM_HOST` prints nothing; every arm's tool list equals the control's. Two are **added** here, because the inherited set does not cover a prompt sweep: **every r0b exemplar disjoint from the bank and the anchor set**, and **every arm's prompt hashed per run in a per-arm manifest**. The prompt hash is new work, not an inherited invariant — `compare_runs.py` gates the bank, the corpus fingerprint and config divergence but has **no prompt-identity check** at all (the configuration file and its digest are printed as provenance only, `compare_runs.py:476-490`), and `campaign.lock` hashes the prompt as **one fixed sha256** for a whole campaign (`scripts/benchmarking/feature_matrix/lib.sh:155`, `:158-161`), which a prompt sweep varies by design, so the lock cannot be reused unchanged; the prompt moves into a per-arm manifest the way `arms` already holds per-arm YAML hashes (`lock_campaign.sh:58-68`). **A void arm reports no numbers** |
| Primary metrics, per arm vs control | **source accuracy** — McNemar exact test, paired per question, p < 0.05 in **both** runs (the pattern [#498](https://github.com/fasrc/archi/issues/498) used). The test is **not in this repository**: run `mcnemar_exact` from `feature_matrix/figures/extract_figure_data.py:67-74` of `fasrc/archi-bench-out` against the sweep artifacts, or port it into `compare_runs.py` first (W6) — a raw hit-rate change is not a verdict. **item pass rate** and **atom score** — from `archi eval qa` runs joined with `compare_runs.py --qa-run LABEL=RUN_DIR` and paired through `qa_block`, whose rows carry `mean` and `se` only, so the 2σ comparison against the four-run floor (pass 0.424 ± 0.024, atom 0.475 ± 0.017) is computed by hand from those: `--noise-floor` accepts the five RAGAS metric names only (`compare_runs.py:75-81`, rejected at `:682-686`) |
| Guard (G8) | a `helps` verdict is downgraded to `mixed` if any RAGAS metric, the QA pass rate, or an `easy_retrieve` anchor regresses by more than one σ, or if the `should_refuse` anchor fails — the campaign's rule, unchanged |
| Cost side, always reported | **blowout count** vs 7 / 109 — a rise is `hurts` regardless of accuracy; **time per question** vs 48.2 s; Δ degraded-row count |
| Descriptive only | `required_atom_recall` (a per-item fraction, not paired-binary, so McNemar does not apply); all RAGAS means (MDE 0.025–0.05, ~40 runs per arm); `context_precision` ranks the leaderboard and is **not** the verdict |
| Preflight census | KB-article coverage of `category` ≥ 90 %; the 19-label prompt list equals the distinct set in `documents.extra_json->>'category'` on the sweep's own stack — a mismatch is an ingest regression, not a reason to edit the prompt. This census runs against a stack that already exists — the post-D1 stack after W4, or the sweep's own stack before a `run_arm.sh --rerun` second run — because on a fresh `archi evaluate` the benchmark container waits only on Postgres and the config seed (`src/cli/templates/base-compose.yaml:717-721`) and starts scoring as soon as its in-container ingest wait clears (`src/bin/service_benchmark.py:1226`), leaving no window on that path |
| Provenance | the sweep's stack snapshots its URL → category map at archive time (§6.1) so the per-category slice of these runs is reproducible |
| Verdict | `helps` only if a primary metric clears its threshold in both runs **and** the guard holds **and** blowouts do not rise; `hurts` if any primary regresses past its threshold or blowouts rise; else `no measurable difference` |
| Ceiling note | `force_initial_retrieval` is on, so r0a shapes only follow-up searches. If r0a shows nothing, one follow-up config arm with the flag **off** is permitted before the question closes |

**Exit:** two runs per arm scored; the verdict per arm posted on the tracking issue with the
numbers and the void-check record; the artifacts and the category snapshot committed to
`fasrc/archi-bench-out`.

### Phase 2 — Decide from the verdict

| Outcome | Action |
|---|---|
| Both arms `no measurable difference` | Post the finding. Close the tracking issue with a recorded decision. Categories stay a free ingest field. **No code.** |
| r0a `helps` | File a rung-1 issue with the verdict record in its body. It enters a milestone **only if it clears the gate bar** (`AGENTS.md:16-18`); a `helps` verdict is evidence for that judgment, never a substitute for it. Rung 1 is: `category` in the chunk header (`src/archi/pipelines/agents/tools/retriever.py:94`); `category` and its distinct values through `api_catalog_schema` **and** the `list_metadata_schema` formatter (`src/archi/pipelines/agents/tools/local_files.py:469-476`), because the formatter drops any field it does not name; one dedicated default-off toggle |
| r0b `helps` | Static ICL is a prompt change and needs no code. Ship the r0b section into archi-config `agents/claw/fasrc-docs.md` and the host spec after the blowout read. Dynamic ICL is a separate proposal with an exemplar pool **disjoint from the bank** (Argilla feedback, ticket traffic) and its own contamination record |
| Either arm `hurts` or `mixed` | Record it. The question closes for that arm. A `mixed` arm does not advance |
| Blowouts rise on either arm | The arm is `hurts` regardless of accuracy. Token cost per turn is the cause to check first ([#499](https://github.com/fasrc/archi/issues/499), [#263](https://github.com/fasrc/archi/issues/263)) |

### Phase 3 — Rung 2, only on a rung-1 verdict

A retrieval filter with fallback. Its shape is fixed now so nobody designs it twice:

- Plumb `filter` through **`LlamaIndexHierarchicalRetriever`**, not only `HybridRetriever`.
  The hierarchical path is the default.
- **Whitelist the filter keys** before they reach
  `src/data_manager/vectorstore/postgres_vectorstore.py:351` and `:482`. The key is
  interpolated into SQL. A model-chosen key must never reach it unchecked.
- Run filtered, then re-run unfiltered when the filtered set is empty or below a threshold.
  Never a bare `WHERE`.
- One dedicated default-off toggle. Dark until the release that claims it.
- Share the key and the predicate with `collection` routing. A category filter and a
  collection filter are one mechanism at two grains.

The five strikes from July 2026 still apply to any **score boost**. This plan does not
revive a boost. A filter with fallback has a different failure mode (a missed document, not
a manufactured one) and the fallback bounds it.

---

## 6. How categories get evaluated

The campaign measures the whole bank. It cannot say which documentation section moved. This
is the gap the request names, and the fix is small — but only if the slice records which
corpus it read.

### 6.1 A per-category slice of the golden set, with provenance

Join each bank row's `sources` URL to a document's URL and read that document's
`category`. The join gives every gold row a documentation category with no hand labels.
The bank keeps no new field, which obeys the July rule that no authored label feeds a
metric.

The join must **not** read live Postgres. A live join changes the slice whenever the
corpus is re-ingested, without any change to the scored answers. So:

- **Snapshot at archive time.** A matching corpus fingerprint does not prove a snapshot
  carries the categories the run used: `CORPUS_STATE_QUERY` hashes document size, chunk text
  and parent text only, and `documents.extra_json` — where `category` lives, there being no
  column for it (`src/cli/templates/init.sql:235`) — is never hashed
  (`src/bin/service_benchmark.py:106-129`; metadata appears once, at `:126`, as a join key).
  A re-ingest overwrites `extra_json` in place (`catalog_postgres.py:335`), so a
  metadata-only change moves the category map at a constant fingerprint. Hence: when a run
  is archived (`scripts/benchmarking/feature_matrix/archive_run.sh` today), dump
  `SELECT url, extra_json->>'category' FROM documents WHERE NOT is_deleted` to a
  `category_map.json` next to the artifact, and record its sha256 and the corpus
  fingerprint in the ledger entry. W6 has to add a **multi-arm archive path** to do this:
  `archive_run.sh` exits when an artifact holds anything but one arm
  (`scripts/benchmarking/feature_matrix/archive_run.sh:112-113`), and a prompt sweep emits
  one artifact carrying every arm's entry (`src/bin/service_benchmark.py:695`). The same
  script also requires a two-digit arm label, a campaign lock, a stack lock, a `ragas-start`
  ledger row and factor-key agreement with the arm YAML (`:35-38`, `:50`, `:80`, `:124-133`),
  so this is not a one-line relaxation.
- **Read only a matching snapshot.** The slice reads the snapshot whose fingerprint equals
  the artifact's. No snapshot, or a fingerprint mismatch, means **no slice** for that run,
  and the report says so.
- **Where:** a report change in `scripts/benchmarking/compare_runs.py`, behind tests. The
  script already slices by `anchor_type` and `difficulty` (`SLICE_FIELDS`, `:85`); a derived
  `category` slice joins at that seam. Not a schema change. Not a bank edit.
- **URL canonicalization:** both sides, as PR [#106](https://github.com/fasrc/archi/pull/106)
  did for trailing slashes. Report unresolved gold sources. Do not drop them.
- **Output:** per category — gold rows, distinct gold articles, source accuracy, item pass
  rate, blowouts. Any category with fewer than **3 distinct gold articles** is reported as
  **underpowered** and carries no verdict.
- **First use is the rung-0 sweep, not the archive.** The `fm-00` and `fm-03` stacks are
  gone (`docker ps`, 2026-09-19), so no snapshot with a matching fingerprint can be taken
  for the arm-00 and arm-03 artifacts. A slice of those runs against the 841-document
  claw corpus reads a different corpus and is **not admissible**. The bank-coverage
  census below runs today; the per-run slice starts with the first run that ships a
  snapshot.

### 6.2 Three preflight censuses, scripted

1. **Coverage:** KB-article coverage of `category` (denominator: `url LIKE '%/kb/%'`).
   Below 90 % is an ingest bug to chase first.
2. **Vocabulary drift:** the distinct set in `documents.extra_json->>'category'` equals the
   19-label breadcrumb list in any prompt that **routes on categories**. A mismatch fails the
   preflight. A label list under a disabled processor (`categorization.categories` while
   `categorization.enabled: false`, per D1) is **retired, not drift**, and is out of scope:
   §3 retires it by deleting the list, not by failing this census. The narrow scope is
   deliberate. A broad reading — "any prompt or config" — fails by construction after D1,
   for three independent reasons: D1/W4 flip only `enabled` and leave the sibling
   `categories:` list in place (`src/cli/templates/base-config.yaml:519`, `:524-527`); this
   repository's own `docs/docs/configuration.md:688-692` carries a third, four-label list;
   and after D1's re-ingest nothing writes `llm_category` at all, so the Postgres set is
   empty against a six-label file.
3. **Bank coverage by category:** join the bank's gold URLs to the current corpus's
   categories and count distinct gold articles per category. This one is a census, not a
   slice of scored answers, so a live read is admissible. It tells us **before** the sweep
   which of the 19 categories the bank can see at all and which are underpowered.

The first two are one `psql` query each today (archi-config #22
`benchmarking/prompt_sweep_r0/README.md` §1). Phase 1 turns all three into a script with a
test so they run the same way every time.

### 6.3 Statistical rules, restated once

- Verdicts come from **count-type metrics** paired per question, in both runs, with the
  campaign's void checks and guard applied.
- RAGAS means are descriptive at two runs.
- A per-category claim needs at least 3 distinct gold articles in that category and a
  fingerprint-matched snapshot.
- An ICL exemplar must come from outside the bank and its anchor set, and the
  disjointness check is recorded with the arm.

---

## 7. Release-plan judgment

- **The sweep enters no milestone.** It is evidence work. Its tracking issue carries
  `evidence-trial` and closes with a recorded decision (D3).
- **#496 must carry a milestone or `parked`** (D5). Today it carries neither.
- **Rung 1 and rung 2 enter a milestone only with a verdict** from Phase 2, through the
  gate bar. If that release is not the next one, the code ships dark.
- **Nothing here touches the four `v2026.10.0` gates** (#216, #215, #384, #396). D1 is
  #396's own disposition and does not change #396's scope.
- **Interactions to read, not act on:** [#497](https://github.com/fasrc/archi/issues/497)
  (reranker off gives 0 / 5 blowouts) and [#498](https://github.com/fasrc/archi/issues/498)
  (k = 8) change the retrieval baseline any later category arm compares against. Lock the
  sweep after those decisions land, or record which state it ran under.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| D1 changes the corpus fingerprint and invalidates cross-arm comparison | D4: D1 before the sweep lock; record the fingerprint on the tracking issue |
| Both arms add ~500 tokens per turn to a system with 8.3 % blowouts | Blowout count is on the cost side of every verdict; a rise is `hurts` |
| A two-run count verdict advances code on noise | Both runs must agree; the campaign's void checks and G8 guard apply; a `mixed` arm does not advance |
| r0a's substring filter is invisible to 628 non-KB documents | r0a's fallback rule; the bank-coverage census shows how many gold rows are non-KB (today: 0, per [#490](https://github.com/fasrc/archi/issues/490)) |
| A per-category slice reads a corpus other than the one scored | The slice reads only a fingerprint-matched snapshot; no snapshot, no slice |
| A model-chosen filter key reaches SQL | Rung 2 whitelists keys; nothing in rung 0 or 1 passes a key to the vector store |
| The bank cannot see most categories | The bank-coverage census runs before any verdict is read |
| `anchor_type` means question kind in the live bank but holds difficulty values (`easy`/`medium`/`hard`) in archi-config `benchmarking/queries.json` | The slice runs on one bank per comparison; the void checks refuse a comparison that mixes banks |
| r0a depends on an untested substring fallback that a refactor can remove without notice | W7 pins `_build_extra_text` and the fallback with a unit test before the sweep runs |
| The 841-document claw corpus differs from the 1 091-document campaign corpus | The sweep runs on one stack; the census is re-read on that stack, not copied from the README |

---

## 9. Work items

| # | Item | Type | Where | Depends on |
|---|---|---|---|---|
| W1 | Amend PR #511 per Appendix A; react to each Codex comment | docs | fasrc/archi | — |
| W2 | Update archi-config PR #22 body; merge | docs | fasrc/archi-config | — |
| W3 | Archive `measure-category-boost-ceiling` as shelved | chore | fasrc/archi | D6 |
| W4 | `categorization.enabled: false` in dev.yaml, ragas.yaml, host config; template comment + docs; redeploy; record ingest time | config + docs + deploy | both repos, FASRC host | D1 |
| W5 | File the `evidence-trial` tracking issue with the Phase 1 pre-registration | tracker | fasrc/archi | D3 |
| W6 | Category snapshot at archive time + fingerprint-matched per-category slice in `scripts/benchmarking/compare_runs.py` (§6.1); plus the paired exact (McNemar) test on per-question source hits and on per-question ok/not-ok, ported from `archi-bench-out`'s `mcnemar_exact`; with tests | code | fasrc/archi `scripts/benchmarking/` | — |
| W7 | Preflight census script (§6.2, all three censuses), with tests; run the bank-coverage census and post the table; plus one unit test that pins `_build_extra_text` and the `search_metadata` substring fallback r0a depends on; plus an r0b exemplar-disjointness check — every r0b exemplar question and its cited URLs absent from `benchmarking/fasrc_ragas_queries.json` and from the anchor set — with its result recorded on the arm | code | fasrc/archi `scripts/benchmarking/`, `tests/unit/` | — |
| W8 | Run the rung-0 sweep: per replicate, one `archi evaluate --config-dir` pass **and** one `archi eval qa` pass per arm (6 + 6 runs over the two replicates, ≈ +6–9 h), joined with `compare_runs.py --qa-run LABEL=RUN_DIR`; post verdicts with the void-check record. Without the QA runs `qa_block` returns nothing (`compare_runs.py:1625-1626`) and both QA primaries are silently absent | measurement | claw or FASRC host | W2, W4, W5, W6, W7 |
| W9 | Phase 2 decision recorded on the tracking issue | tracker | fasrc/archi | W8 |
| W10 | Rung-1 issue with the verdict record (only on `helps`) | tracker | fasrc/archi | W9 |

W6 and W7 are the only code in this plan before a verdict exists. Both are report and
preflight code under `scripts/benchmarking/`. Neither touches the answer path.

---

## Appendix A — Corrections to the companion proposal

The Codex review on PR [#511](https://github.com/fasrc/archi/pull/511) posted eight inline
findings. Each was checked against `origin/dev` @ `4b253e26`.

| Finding | Verdict | Action in PR #511 |
|---|---|---|
| Arm 03 was not mechanically inert: `search_metadata` reads `extra_text`, which carries `llm_category:<value>`, through a tool the campaign prompt exposes | **Correct** (`src/data_manager/collectors/utils/catalog_postgres.py:474-481`, `:1560-1569`) | Rewrite the TL;DR claim: "unread by the retriever and the prompt", not "no reader" |
| The default retriever is `LlamaIndexHierarchicalRetriever`, which calls `hybrid_search` directly; a rung-2 filter on `HybridRetriever` is unused by default | **Correct** (`src/data_manager/vectorstore/retrievers/hierarchical_retriever.py:133-138`, `src/data_manager/vectorstore/retrievers/factory.py:54-56`) | Rung 2 names the hierarchical retriever |
| `list_metadata_schema` hard-codes its three output fields; rung 1 must change the formatter | **Correct** (`src/archi/pipelines/agents/tools/local_files.py:469-476`) | Rung 1 names the formatter |
| `force_initial_retrieval` defaults on and is not a dark-ship precedent | **Correct** (`src/archi/pipelines/agents/fasrc_docs_agent.py:256`) | Rungs 1 and 2 get dedicated default-off toggles |
| `required_atom_recall` is fractional; McNemar is wrong; `qa_block` pairs only `item_pass_rate` and `atom_score` | **Correct** | Pre-registration moves it to descriptive |
| The rung-0 tracking issue must exist, labelled `evidence-trial`, before the sweep | **Correct** on the invariant; the proposal's "do not file yet" hides the sweep from every report | D3 |
| The soft-hint arm advertises a substring predicate with no fallback, so rung 0 no longer measures "nothing excluded" | **Real gap, overstated**: r0a's own text orders the fallback ("search again without the narrowing"), so the prompt supplies the fallback the code lacks. The measured arm is "soft hint plus a model-enforced fallback", and the proposal must say so | Rename the rung-0 description |
| `src/archi/pipelines/agents/base_react.py:1696-1697` is the pre-loop trim; the in-loop counter is `src/archi/pipelines/agents/utils/context_budget.py` (15 % reserve, 25 % margin) | **Correct** (`src/archi/pipelines/agents/utils/context_budget.py:108`, `:144`) | Fix the citation |

Two more corrections come from this plan's own reads:

- The LLM label list is **6** values, not 20 (archi-config `environments/dev.yaml:220-226`
  on `main`, `:101-106` at the pin).
- The 19-item list is the breadcrumb vocabulary and lives in the corpus, not in any config.

Stale anchors in other companion documents, for whoever next edits them:

- [Release Plan 2026](release-plan-2026.md) row #396 cites `processing.py:689` for the
  categorization default; it is `src/data_manager/collectors/processing.py:1078` today.
- `docs/docs/configuration.md:685-692` shows a **4-label** example set — a third label list
  in the docs beside the 6 in the deploy configs and the 19 on the corpus.
- [Multi-Collection Routing](multi-collection-routing.md) line 42 says RBAC has "8 categories,
  20+ permissions". `src/utils/rbac/permission_enum.py:24-70` defines 11 groups and **26**
  permissions, so "20+" is right and "8 categories" is wrong — it is 11. An earlier draft of
  this appendix said 23 permissions; that was also wrong.

---

## Appendix B — Work order for an autonomous session

The text below is a self-contained goal statement for a session that executes this plan.
It is written to be pasted after `/goal`.

```text
Goal: land the "categories" action plan for fasrc/archi (docs/docs/proposals/categories-action-plan.md)
through Phase 0 and the code items W6–W7, and stop at the human gates.

Ground truth: the plan document, the release plan (docs/docs/proposals/release-plan-2026.md),
the campaign proposal (docs/docs/proposals/feature-matrix-campaign-2026.md), CLAUDE.md, and the
code at origin/dev. Re-verify every file:line you cite against origin/dev before you act on it;
the checkout lags.

Do, in order:
1. W1 — DONE, no action. PR #511 ran five review rounds and merged on 2026-09-21 (squash
   9705e588); Appendix A's verdicts were folded in before it landed. Start at step 2.
2. W3 — PREPARE the archive move for openspec/changes/measure-category-boost-ceiling/
   (shelved, banner kept) as a docs-only PR to dev, but do NOT apply it or open the PR
   without a recorded D6 decision. D6 is a human gate: with no decision recorded, leave the
   prepared diff in the final report and treat this step as complete.
3. W6 — Two parts, test-first. (a) At archive time, dump url → extra_json->>'category' for
   every non-deleted document to category_map.json next to the artifact and record its sha256
   and the corpus fingerprint in the ledger entry. (b) Add a derived category slice to
   scripts/benchmarking/compare_runs.py at the SLICE_FIELDS seam that reads ONLY a snapshot
   whose fingerprint matches the artifact; report per category the gold rows, distinct gold
   articles, source accuracy, item pass rate, and blowouts; mark categories with fewer than 3
   distinct gold articles as underpowered; canonicalize URLs on both sides; report unresolved
   sources; with no matching snapshot, print "no slice" and say why. Do not run it against the
   arm-00 or arm-03 artifacts: their stacks are gone and no matching snapshot exists.
4. W7 — Add a preflight census script, test-first, read-only against Postgres: KB-article
   coverage of category (fail below 90 %), vocabulary drift between
   documents.extra_json->>'category' and the 19-label breadcrumb list in any prompt that routes
   on categories (fail on mismatch; a retired list under categorization.enabled: false is out of
   scope), and bank coverage by category (distinct gold articles
   per category, from the bank's gold URLs). Run the bank-coverage census against postgres-claw
   and put the table in the PR body, labelled with the corpus fingerprint it read. In the same
   PR add one unit test that pins _build_extra_text's key:value emission and the
   search_metadata substring fallback for an unknown key; r0a depends on both and neither has
   a test today.
5. W5 — Draft the evidence-trial tracking issue body from Phase 1's pre-registration table and
   save it as a file in the PR; do not file the issue.

Rules:
- Branch from origin/dev per PR; gate green before every commit; adversarial review before every PR.
- No change to the answer path, the retriever, the vector store, the prompts, or any config.
- No redeploy, no re-ingest, no sweep run, no issue filed, no label changed. Those are D1–D7 and
  W4, W5, W8: human gates. Stop and report when you reach one.
- One document vocabulary: the breadcrumb metadata["category"]. Do not introduce a label list.
- Do not port upstream #570.

Done when: W6 and W7 open as PRs against dev, and W3 prepared (opened only if D6 is recorded),
with green gates and review findings addressed; the W5 issue body drafted; a final report
lists each PR URL, the bank-coverage table, and the decisions D1–D7 that still need a human.
```
