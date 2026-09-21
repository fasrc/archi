# In-Context Learning and Query→Category Mapping

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** September 2026
**Status:** Exploration — no milestone proposed; the rung-0 tracking issue is filed as `evidence-trial` before the sweep runs (see "Release-plan judgment")
**Baseline read:** `origin/dev` @ `4b253e2`, 2026-09-19; every anchor re-checked against `origin/dev` @ `314039d`, 2026-09-21
**Companion:** [Release Plan 2026](release-plan-2026.md) · [Multi-Collection Routing](multi-collection-routing.md) · [Feature-Matrix Campaign](feature-matrix-campaign-2026.md) · [Categories Action Plan](https://github.com/fasrc/archi/blob/docs/categories-action-plan/docs/docs/proposals/categories-action-plan.md) (PR #512, unmerged; the link becomes relative when it lands)

---

## TL;DR

Two ideas were raised:

1. **In-context learning (ICL)** — put worked examples in front of the model.
2. **Query→category mapping** — have the agent map the query to a category in
   `docs.rc.fas.harvard.edu` before answering.

Both are **prompt-only at rung 0** and need **no code at all** to measure, because
`generate_prompt_sweep.py` already A/B's agent prompts with everything else held fixed.

The load-bearing point for idea 2 is that it is **half-built and unread where it would
matter**. [#496](https://github.com/fasrc/archi/issues/496) records the LLM label as
write-only, and the same holds for the breadcrumb field: the FASRC docs category is
captured at ingest into `metadata["category"]` (`processing.py:181-208`), and **neither
the retriever nor the prompt path reads it** — not the retriever, not the chunk header
the model sees, not the schema hint tool. One reader does exist, by accident: both labels
land in the catalog's `extra_text`, and `search_metadata_index` matches an unknown key
such as `llm_category:<value>` as a substring filter against it
(`catalog_postgres.py:474-481`, `:1560-1569`). The campaign prompt exposes that tool but
never names either key, and `list_metadata_schema` never advertises them, so the model
could reach the label only by guessing it. That is why "no effect" was the **expected**
outcome of arm 03 ([#496](https://github.com/fasrc/archi/issues/496): 19.2 min per
ingest, no measurable retrieval effect) — not the only possible one, and the null result
cannot be attributed mechanically to a writer with no reader. **Arm 03 is evidence
against the ingest-side label as wired and as prompted. It is not evidence against
mapping the query to a category.**

Neither idea clears the release plan's gate bar today. Rung 0 runs on the
**prompt-sweep harness**, which is the vehicle the feature-matrix campaign
([#396](https://github.com/fasrc/archi/issues/396)) explicitly points at for this: that
campaign puts prompt variants **out of scope** (§11) and pins the agent prompt as a fixed
factor by sha256 (§2), so a prompt arm is not an arm of it and needs its own
pre-registration. What #396 does supply is most of what makes the arm measurable — its
power numbers, its decision rules, `compare_runs.py`, and the arm-03 result above. What it
does **not** supply is a comparator: its baseline runs were pinned to one code SHA and one
corpus, so a standalone sweep must carry the unchanged production prompt as a control arm
of its own, and it must bring its own paired tests for the count metrics, because
`compare_runs.py` has none (see "Staging").

---

## What exists today

### The category is captured, and nothing reads it

| Stage | Where | State |
|---|---|---|
| Breadcrumb → `metadata["category"]` | `processing.py:181-208` (`HtmlCategoryProcessor`) | **live** — default on, runs whenever `html_to_markdown.enabled` (`processing.py:1072-1076`) |
| LLM label → `metadata["llm_category"]` | `processing.py:857` (`CategorizationProcessor`) | **live on FASRC**, code-default `false` (`base-config.yaml:519`, `processing.py:1078`) |
| Catalog persistence | `catalog_postgres.py:295` → `extra_json` / `extra_text` | **live** — `_build_extra_text` (`:1560-1569`) emits `category:<value>` |
| Chunk metadata | `manager.py:484,574` (`file_level_metadata` merged into every chunk) | **live** — the field reaches `document_chunks` |
| Retrieval filter | `LlamaIndexHierarchicalRetriever._generate_candidates` (`hierarchical_retriever.py:125-138`) on the shipped default — the deployed configs set `hierarchical_rerank.enabled: true` and `factory.py:54-56` returns it; `HybridRetriever._get_relevant_documents` (`hybrid_retriever.py:63-116`) only when reranking is disabled (`factory.py:79`) | **absent on both paths** — each calls `hybrid_search(query, k, weights)` and forwards no `filter`, though `PostgresVectorStore.hybrid_search` accepts one (`postgres_vectorstore.py:439,449`) |
| What the model is shown | `_format_documents_for_llm` (`tools/retriever.py:78-98`) | **absent** — header is `[i] title <url> (hash=…)` + `Score:`; no category |
| Schema hint to the model | `api_catalog_schema` (`uploader_app/app.py:779-790`) → the `list_metadata_schema` tool (`create_metadata_schema_tool`, `tools/local_files.py:439-476`) | **absent** — `keys` is `sorted(_METADATA_COLUMN_MAP.keys())` (`catalog_postgres.py:49-66`), `get_distinct_metadata` allows only `source_type`, `suffix`, `ticket_id`, `git_repo`, `url` (`:554-564`), and the tool formats exactly three payload fields, `keys`, `source_types`, `suffixes` (`local_files.py:469-476`). `category` is in none of them |
| Query-side classification | — | **absent** |
| Few-shot / exemplar machinery | — | **absent**: zero hits for `few.?shot|in.?context learning|\bICL\b|exemplar` across `src/`, `docs/`, configs |

`grep -rn '"category"' src/ --include='*.py'` outside `data_manager/collectors/` and
`evaluation/qa/` returns nothing. The field is write-only, exactly as
[#496](https://github.com/fasrc/archi/issues/496) records for `llm_category`.

### The taxonomy lives only in the breadcrumb

The FASRC docs site is an Echo Knowledge Base install: the ingested sitemap is
`https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml`
(`examples/sources.manifest.yaml:12`) and article URLs are flat `/kb/<slug>` — see the
anchor questions' `sources` (`examples/benchmarking/anchor_questions.json`). There is no
category segment in the URL to key off. The taxonomy exists on-page as
`Home › <Category> › <Article>`, which is precisely what `_extract_kb_category`
(`processing.py:130-149`) scrapes off `span.eckb-breadcrumb-link`.

**So "the category present in docs.rc.fas.harvard.edu" is a field archi already
has.** That is the good news for idea 2, and it is why the idea is cheaper than it
looks.

### One usable path is already open, by accident

`search_metadata` maps an unrecognised filter key to a substring match on the flattened
metadata blob (`catalog_postgres.py:474-481`):

```python
column = _METADATA_COLUMN_MAP.get(key)
if column:
    sub_clauses.append(f"{column} = %s")
else:
    sub_clauses.append("extra_text ILIKE %s")
    params.append(f"%{key}:{val}%")
```

Because `_build_extra_text` writes `category:<value>`, a
`search_metadata_index` call with `category:storage` **works on the deployed corpus
today**, with no code change. Three caveats before leaning on it:

- It is `ILIKE` substring, not equality: `category:compute` also matches
  `llm_category:compute`, and a prefix matches a longer label.
- `list_metadata_schema` never advertises `category` and offers no distinct values, so
  the model cannot discover the vocabulary — a prompt would have to hard-code it.
- `search_metadata_index` returns matched lines + hashes, not ranked chunks
  (`fasrc_docs_agent.py:86-93`), so it is a different retrieval surface from
  `search_vectorstore_hybrid` and needs `fetch_catalog_document` to read anything.

### Where a prompt lands, and where anything dynamic would land

The agent prompt is **static per deployment**: agent spec markdown →
`load_agent_spec` (`agent_spec.py:62-68`) → `_build_system_prompt`
(`base_react.py:1521-1531`) → `create_agent(system_prompt=…)` (`:1535-1545`). Nothing
varies it per turn.

The per-turn seam that does exist is `_inject_forced_retrieval`
(`base_react.py:1752-1762`, overridden at `fasrc_docs_agent.py:237-302`). It already
rewrites the message list before the model's first turn, prefilling a *completed* tool
round — an `AIMessage` carrying the call plus its `ToolMessage` result. Anything
dynamic (retrieved exemplars, a classified-category hint, a pre-filtered search) belongs
at that seam, and it is a proven pattern rather than a new one.

### The measurement rig, and the bar it sets

`scripts/benchmarking/generate_prompt_sweep.py` renders one benchmarking config per
prompt file, varying **only** `services.benchmarking.agent_md_file` and holding
everything else byte-identical so the leaderboard is apples-to-apples. Both ideas'
rung-0 form is a prompt file, so both are measurable with the rig that already ships.

What the rig holds fixed is the **config**, not the **spec**. `agent_md_file` selects a
whole `AgentSpec`: `load_agent_spec` reads the file's YAML `tools` list
(`agent_spec.py:60-67`, `:134-142`), `BaseReActAgent` takes that list as
`selected_tool_names` (`base_react.py:133`), and `FASRCDocsAgent` builds its vector tools
only if `search_vectorstore_hybrid` is on it (`fasrc_docs_agent.py:52-54`). The generator
checks that each prompt file exists and never parses it
(`generate_prompt_sweep.py:104-110`), and the leaderboard's shared-context check compares
model, provider, judge settings, bank and corpus — never the resolved tool list
(`service_benchmark.py:970-989`). A prompt sweep must therefore **hold the frontmatter
fixed**: identical `tools` across the control and every arm, checked by loading each spec
before the sweep runs. archi-config PR #22's README does exactly that, and its three files
list the same three tools; without the check, a tool-surface change rides along and is
attributed to the wording.

The bar comes from the campaign's own decision rules, as recorded on
[#496](https://github.com/fasrc/archi/issues/496) for arm 03:

- RAGAS deltas are verdicts only outside `MDE = max(2·SE, 2σ)` — **measured at 0.025–0.05**
  per metric on this bank, in both runs. Mean deltas inside that spread need roughly
  **40 runs per arm** to carry a claim.
- Gold-atom deltas only outside 2σ of the four-run floor: pass `0.424 ± 0.024`,
  atom `0.475 ± 0.017`, required-atom recall `0.614 ± 0.010`.
- Count-type metrics were decisive far cheaper: source accuracy `0.868` (McNemar),
  recursion-limit blowouts `7 / 109`, time per question `48.2 s`.

**Consequence for both ideas: judge them on the count-type metrics — source accuracy
and blowout count, each under a pre-registered paired test (McNemar; see "Staging") —
and read the gold-atom metrics, required-atom recall included, only as paired deltas
under the 2σ rule; never on RAGAS means.** The paired test is not in this repository:
`compare_runs.py` reports source accuracy per arm in aggregate and drops blowout rows
before pairing, so a raw count change is not a verdict until the test runs. Required-atom
recall is a per-attempt fraction, `entailed_required / required_count`
(`evaluation/qa/scoring.py:32`), not a paired pass/fail count, so McNemar does not apply
to it; and `compare_runs.py` pairs only `item_pass_rate` and `atom_score`
(`compare_runs.py:1695`), so a required-atom delta is descriptive until that rig is
extended. A two-run RAGAS arm cannot resolve a prompt effect of plausible size, and
reporting one would be the kind of overclaim `v2026.09.0` exists to prevent.

---

## Idea 1 — In-context learning

### Two different features under one name

**Static ICL** is a prompt edit: 3–5 worked examples in the agent spec markdown. Zero
code, sweepable today, and it carries a fixed prompt-token cost on *every* turn.

**Dynamic ICL** (retrieve the nearest prior Q/A and inject it) is a feature: an exemplar
store, a second retrieval per turn, and an injection at the
`_inject_forced_retrieval` seam. It is not a prompt tweak and should not be scoped as
one.

### Two risks that are specific to this repo

**Contamination is the real hazard.** The obvious exemplar source is the material archi
already has: the 109-question golden bank (`evaluation/qa/dataset.py`) and
`examples/benchmarking/anchor_questions.json`. Drawing exemplars from the bank and then
scoring against the bank leaks gold answers into the system under test, and every number
downstream of it becomes uninterpretable — including the numbers `v2026.09.0` is being
built to make citable. Any ICL arm must draw exemplars from a pool **disjoint from the
bank**, and the pre-registration must name the pool and record the disjointness check.
The Argilla feedback loop (#60) and real ticket traffic are the candidate pools; the
bank is not.

**Prompt tokens interact with a live defect.** [#499](https://github.com/fasrc/archi/issues/499)
measures 8% of FASRC questions ending in a recursion-limit blowout at ~230 s. Two bounds
sit under that, and they are different mechanisms: the **pre-loop history trim** reserves
15% of the window (`base_react.py:1696-1697`) and counts with the model's own
`get_num_tokens_from_messages`; the **in-loop approximate counter**
(`count_request_tokens`, `agents/utils/context_middleware.py:103-121`) subtracts, by
default, a 15% generation reserve (`context_budget.py:108`) *and* a separate 25% counting
margin (`:144`) over a 4-chars-per-token estimate (#263, parked). Static exemplars add
fixed tokens to every turn, but **only the in-loop counter sees them**: the pre-loop trim
counts `history_messages` alone (`base_react.py:1703-1705`), while the agent-spec text
travels separately as `system_prompt` into `create_agent` (`:1536-1544`); only
`count_request_tokens` prepends that prompt to what it counts
(`context_middleware.py:118-121`). So the pre-loop trim reserves no room for the examples:
history can sit at its old 85% ceiling before they are added, and the examples land on top
of it. That asymmetry is the overflow-risk mechanism to watch, not a uniform tax under both
bounds. The blowout count is already a campaign metric (`7 / 109`), so the interaction is
measurable in the same arm — but it must be **read**, not assumed benign.

---

## Idea 2 — Map the query to a docs category

### Why arm 03 does not refute it

[#496](https://github.com/fasrc/archi/issues/496) is sometimes readable as "categories
were tried and did nothing." What was tried was the **ingest-side writer**: one LLM call
per document, writing a label that `grep` proves nobody reads. The measured cost was real
(19.2 min per 1,091-document ingest) and the measured effect was nil — which is what a
write-only field must produce. Turning that writer off is the right call and is already
scoped.

Idea 2 is the **reader half**, on the query side. It has never been measured, and arm
03 says nothing about it.

### The design choice that matters

A hard classify-then-filter has a failure mode worse than doing nothing: one
misclassified query removes the correct document from the candidate set entirely, and
the answer degrades silently with full confidence. Ordered by risk:

1. **Soft hint (rung 0).** The prompt tells the agent to name the docs category first,
   then search — the category shapes the query text, and no code excludes anything. If
   the prompt also advertises the `category:<value>` substring filter on
   `search_metadata_index`, rung 2's exclusion risk comes back through the model's own
   tool call, because `search_metadata` has no thin-result fallback
   (`catalog_postgres.py:474-481`); the prompt must then order the unfiltered re-search
   itself, and the arm measures a soft hint **plus a model-enforced fallback**, not a bare
   hint. Either way, the only variant that is free.
2. **Show the category (rung 1).** Add `category` to the chunk header in
   `_format_documents_for_llm`, so the model can see which section each hit came from and
   reject off-category hits itself. One field in one f-string, plus a test, behind a
   toggle whose default is off.
3. **Filter with fallback (rung 2).** Plumb `filter` into `hybrid_search` through the
   retriever the shipped config actually uses — `LlamaIndexHierarchicalRetriever`, whose
   candidate generator calls `hybrid_search` directly — and through `HybridRetriever` for
   the reranker-off path, or through one shared retrieval interface; run filtered, and
   re-run unfiltered when the filtered set is **empty or thin** — *thin* pre-registered
   as fewer than half the requested `k` candidates, a `min_filtered_results` knob under
   the rung-2 toggle with code default `k // 2`. The thin case is the one that matters: a
   wrong category that returns one irrelevant document is the silent exclusion, and an
   empty-only fallback never fires on it. Never a bare `WHERE`.

A fourth cost question decides rung 2's shape: classifying the query with an LLM call
adds latency to the **answer** path (baseline 48.2 s/question), unlike categorization's
ingest-time cost. The prompt-only variant asks the model to state the category as its
first reasoning step and costs no extra call at all — which is another reason rung 0
comes first.

### Two unknowns that must be closed on the host

Neither is answerable from this repository, and both are cheap on the dev host:

1. **Coverage.** What fraction of the 1,091 ingested documents carry a non-empty
   `category`? `_extract_kb_category` returns `None` for crumbless pages and never
   raises (`processing.py:137-149`), so a low-coverage corpus would cap the idea's
   ceiling before any prompt work. One query against `documents.extra_json`.
2. **Vocabulary agreement.** The FASRC config's LLM label list is **six** lower-case
   slugs — `job-scheduling`, `storage`, `account-access`, `software`, `compute`,
   `data-transfer` (`fasrc/archi-config` `environments/dev.yaml:220-226` on `main`,
   `:100-106` at the `deploy-pin-2026-08b` checkout) — not the twenty that an earlier
   draft of this document and [#496](https://github.com/fasrc/archi/issues/496) cite.
   The breadcrumb vocabulary is a **19-item Title Case list that lives only in the
   corpus** (`documents.extra_json` on the host), and the two lists share no values
   ([Categories Action Plan](https://github.com/fasrc/archi/blob/docs/categories-action-plan/docs/docs/proposals/categories-action-plan.md),
   PR #512). #496 says the labels "duplicate the breadcrumb `category` field the scraper
   already writes"; they duplicate its *purpose*, not its values, so a prompt that
   hard-codes one list filters against the other.

The taxonomy in any rung-0 prompt must be **copied from the host, not from this
document and not from any config**: `docs.rc.fas.harvard.edu` was unreachable from the
session that wrote this, and the breadcrumb list is read from the corpus, not from
`archi-config`.

---

## Staging

**Rung 0 — no code, measurable now.** Three prompt files through
`generate_prompt_sweep.py`, in **one** sweep:

- **(control) the unchanged production prompt** — `config/agents/claw/fasrc-docs.md`,
  the campaign's locked prompt (sha256 `ac22702a…4ce8`), run **in the same sweep** as the
  arms. Every delta is read against this arm. The feature-matrix campaign's historical
  baseline is **not** a substitute: that campaign pinned one code SHA and one corpus, and
  this sweep can run after either has moved. `compare_runs.py` refuses arms that disagree
  on `corpus_fingerprint` (G3, `compare_runs.py:511-545`) but only *displays*
  `code_version.digest` for the baseline and treatment (`:466`); it refuses a digest
  mismatch only among noise replicates (`:859-875`). Without a contemporaneous control, a
  count or timing delta can be intervening code rather than the prompt. archi-config
  PR #22's manifest already lists the control first; the campaign numbers below are a
  drift check on the control, not the comparator.
- **(a) category-routing prompt (`r0a`)** — name the docs category and carry its
  vocabulary in the query text. archi-config PR #22's `r0a` also advertises the
  `category:<value>` substring filter on `search_metadata_index`, with the substring
  caveat stated, **and orders the fallback itself**: "if a narrowed search comes back
  empty or off-topic, search again without the narrowing." The code has no such fallback,
  so this arm measures a soft hint plus a model-enforced fallback. A bare-hint arm, if
  ever wanted, is a fourth prompt file without the filter paragraph.

    **What `r0a` can touch under the shipped default.** `force_initial_retrieval`
    defaults to `true` (`fasrc_docs_agent.py:256`), and the hook it gates searches with
    the **raw user question** before the model's first turn (`:268-274`). So under the
    default, `r0a` never names a category before the first search — the harness has
    already issued an unfiltered one — and the arm shapes only the model's follow-up
    searches. This document pre-registers `r0a` accordingly, as **post-retrieval
    rerouting**, not as category-first search. Category-first search is a separate
    pre-registration, and the flag has to be set where the agent will read it. The
    generated sweep YAML does not reach the agent directly: the harness hands the
    runtime only the selected file's benchmarking agent spec, provider and model
    (`service_benchmark.py:1421-1434`), and the agent loads its pipeline config from the
    PostgreSQL-backed active config through `get_full_config()` (`archi.py:31`,
    `config_access.py:73`, reading Postgres at `:15-21`), which is where
    `FASRCDocsAgent` looks `force_initial_retrieval` up (`fasrc_docs_agent.py:256`, via
    `services.chat_app` at `:61-63`). Postgres is seeded from exactly one file: a
    multi-config deployment renders no `config.yaml`, so the seeder takes the
    alphabetically first rendered arm file and seeds its whole `services` block
    (`config_seed.py:30-52`, `:94`). A value held **identical across every arm**
    therefore does arrive and is honoured; a value that **differs between arms** is
    silently replaced by the first arm's, and the per-arm digest cannot tell those arms
    apart, because `effective_config` overlays only `services.benchmarking` onto the
    running config (`benchmark_provenance.py:77`, `:351`). The harness refuses rather
    than publishing the mislabel: `services.chat_app` is not in
    `DIVERGENCE_IGNORED_PATHS` (`benchmark_provenance.py:104-106`), so the mismatch
    lands in `divergence_from_selected_file` and Procedure E stops the comparison
    (`compare_runs.py:569-588`). The rule for this sweep is therefore: set
    `services.chat_app.force_initial_retrieval: false` in the configuration that seeds
    the sweep deployment's Postgres and **redeploy** — editing a rendered file and
    restarting the container is a no-op (`CLAUDE.md`, "Don't-touch / gotchas") — hold it
    identical across control and treatments, never vary it between arms (the flag's own
    docstring invites a per-arm A/B that the seeder cannot express —
    [#523](https://github.com/fasrc/archi/issues/523)), and confirm
    `divergence_from_selected_file` is empty in every artifact before reading a number.
    State in the issue body which of the two sweeps a number came from.
- **(b) static-ICL prompt (`r0b`)** — 3–5 exemplars from a pool disjoint from the
  109-question bank, with the disjointness recorded.

**Prompt contents pinned, not only their paths.** Record the `sha256sum` of every
resolved prompt file — control, `r0a`, `r0b` — in the issue body before the first run,
and beside each arm's artifact afterwards. Nothing in the rig does this for the arms:
the generator writes the prompt's **path** into each arm's config
(`generate_prompt_sweep.py:128`) and never parses the file it names (`:104-110`), so
`config_fingerprint` digests that path string rather than the prompt's bytes
(`benchmark_provenance.py:321`), and the code digest is a content digest of the `src`
package files only (`:379`, `:489`) — a prompt under `config/agents/` sits outside it.
The arm prompts live in archi-config PR #22, which can still change after this
pre-registration, and the arms run sequentially, so an edit between two arms would leave
two runs carrying identical labels, identical provenance and different treatments. The
control already has a pinned hash (sha256 `ac22702a…4ce8`); the arms need theirs for the
same reason. A hash that does not match its pre-registered value voids that arm.

**Frontmatter held fixed.** All three files carry identical `tools` lists, checked by
loading each spec before the sweep (see "The measurement rig"); a mismatch on any arm
voids the sweep.

**Two evaluators per arm, not one.** `archi evaluate` over the sweep directory produces
the RAGAS, source-hit and timing rows; it does **not** produce gold-atom scores. Those
come from `archi eval qa`, which takes the arm's prompt as `--agent-spec`
(`src/cli/qa_eval.py:41-42`, `:184`) and writes its own run directory, and
`compare_runs.py` reports gold-atom deltas only when handed those directories with
`--qa-run LABEL=RUN_DIR`, one per arm (`compare_runs.py:2295-2332`). Rung 0 is therefore
one `archi eval qa` per prompt file — control, `r0a`, `r0b` — with everything but
`--agent-spec` identical, the way `feature_matrix/qa_arm.sh` runs it, followed by one
`compare_runs.py` invocation carrying all three `--qa-run` pairs. Without the QA runs the
required-atom and other gold-atom deltas do not exist.

**Decision rules, pre-registered.** The decisive metrics are the count-type ones, and
each needs a **paired** test with its threshold written into the tracking issue before
the first run.

**One primary endpoint per arm.** Two arms times two count tests is a family of four,
and "Release-plan judgment" below treats a single positive count result as the evidence
that lets a rung-1/2 issue enter a milestone. Four such tests, each read at an
unadjusted `p < 0.05`, carry a family-wise false-positive rate near 18.5%, so an
unadjusted family lets chance alone supply release-gating evidence. This
pre-registration therefore names **one primary count endpoint per arm**, picked by what
the arm acts on: **source accuracy for `r0a`**, which reroutes retrieval and so changes
which documents come back, and **blowout count for `r0b`**, whose exemplars lengthen
every prompt and so act on whether a row finishes. The primary carries that arm's
verdict at `p < 0.05`. The other count test on each arm is **secondary**: report it with
a Holm-adjusted p-value across the two secondaries, and never let a secondary alone gate
a milestone. Write both primaries into the issue body before the first run; a swap
afterwards voids the sweep.


- **Source accuracy** — exact two-sided McNemar on per-question source hits, control vs
  arm, verdict at p < 0.05. This is the test behind the campaign's `0.868` reading
  ([#496](https://github.com/fasrc/archi/issues/496)), and it does **not** live in this
  repository: `compare_runs.py::source_block` reports each arm's aggregate hit rate beside
  a recomputation (`compare_runs.py:1091-1117`) and pairs nothing, and `timing_block`
  (`:1119`) is descriptive. The campaign's implementation is `mcnemar_exact` in
  [`feature_matrix/figures/extract_figure_data.py:67-74`](https://github.com/fasrc/archi-bench-out/blob/main/feature_matrix/figures/extract_figure_data.py#L67-L74)
  of `fasrc/archi-bench-out`, applied to paired hits at `:329-351`. Run that script
  against the sweep's artifacts, or port the test into `compare_runs.py` first; a raw
  hit-rate change is **not** a verdict.
- **Blowout count** — a blowout is a row whose harness `status` is not `"ok"`
  (`service_benchmark.py:2069-2071`, `:2104`); `compare_runs.py` drops such rows from
  every paired mean (G6, `compare_runs.py:20`, `:207`) and never counts them. Count them
  per arm the way the same bench-out script does (`:167`) and test with the same paired
  McNemar on per-question ok/not-ok, control vs arm, at p < 0.05. Campaign reference:
  `7 / 109` on the locked prompt.
- **Time per question** — descriptive (`timing_block`); report cold and warm means beside
  the control's and read no direction from them.

Gold-atom scores are paired deltas against the 2σ floor: `item_pass_rate` and
`atom_score` are paired by `compare_runs.py` today (`compare_runs.py:1695`), required-atom
recall is descriptive until it is. Do **not** read RAGAS means at two runs.

**Rung 1 — small, only if rung 0 shows signal.** Surface `category` in
`_format_documents_for_llm` (`tools/retriever.py:94`), and teach the whole schema-hint
path to offer `category` and its distinct values: `api_catalog_schema`
(`uploader_app/app.py:779-790`) and `get_distinct_metadata` (`catalog_postgres.py:554-564`)
on the server side, **and** the agent-facing `list_metadata_schema` tool
(`create_metadata_schema_tool`, `tools/local_files.py:469-476`), which formats exactly
three payload fields and hard-codes its tool description — a new field in the payload
never reaches the model until that formatter and description change too. Note the
server half is not one line: `category` is not a promoted column, so distinct values need
a JSONB query over `extra_json` or a schema migration. Both surfaces ship behind one
toggle whose default is off (below).

**Rung 2 — a feature, with its own issue.** `filter` plumbed into `hybrid_search` on
**both** retrieval paths — `LlamaIndexHierarchicalRetriever._generate_candidates`
(`hierarchical_retriever.py:125-138`), which the shipped `hierarchical_rerank.enabled:
true` selects (`factory.py:54-56`), and `HybridRetriever` (`hybrid_retriever.py:63-116`)
for the reranker-off fallback (`factory.py:79`) — or one shared retrieval interface that
both implement; a query-classification step at the `_inject_forced_retrieval` seam;
fallback when the filtered set is **empty or thin**, with *thin* the pre-registered
`min_filtered_results` threshold from "The design choice that matters" (fewer than half
the requested `k`, default `k // 2`) — an empty-only fallback leaves the one-wrong-document
case silent. A filter on `HybridRetriever` alone is unused on the default path.
Dynamic ICL joins here, sharing the same seam and the same exemplar
store. Both overlap [Multi-Collection Routing](multi-collection-routing.md) — a category
filter and a collection filter are the same mechanism at different granularity, and the
two should not grow separate vocabularies.

Rung 1 touches `tools/retriever.py`, `tools/local_files.py` and `uploader_app/app.py`.
None of them is `chat_app/app.py`, so the diff-cover trap in `CLAUDE.md` does not apply,
but all three are large files — run the `black-seam-scout` check before editing in place.

---

## Release-plan judgment

**Neither idea clears the gate bar.** `v2026.10.0`'s feature is *measurably better
answers*, and it is not broken, wrong, or dishonest without either of these: its gates
are #216, #215, #384 and #396. Both ideas are enhancements with no file:line defect and
no measured number behind them, and the plan's own accounting is explicit that a real
defect is not the bar — an unmeasured enhancement is further from it still.

**Rung 0 is not an arm of #396.** That campaign lists *prompt variants* in its
out-of-scope section (§11, "the prompt-sweep harness exists for that") and pins the agent
prompt as a fixed factor (§2, `config/agents/claw/fasrc-docs.md`, sha256 `ac22702a…4ce8`),
so varying the prompt breaks a fixed factor and must not run under the campaign lock. Rung
0 is a standalone sweep with its own pre-registration, reusing the campaign's decision
rules but **not** its baseline runs: the unchanged production prompt runs as a control arm
in the same sweep, and the campaign's numbers serve only as a drift check on that control
("Staging"). Those rules still do the rest: a *no measurable difference* on a free prompt
change is a finding that closes the question, and a positive count-metric result **under
the pre-registered paired test** — not a raw count change — is the evidence a rung-1/2
issue would need to enter a milestone honestly.

Because it is its own sweep rather than a campaign arm, the house pattern for recording it
is a separate issue — which is how #496, #497 and #498 were filed out of the campaign —
not a comment on #396.

**File the rung-0 issue before the sweep runs, labelled `evidence-trial`.** The plan's
invariant is that every open issue carries exactly one of {a milestone, `parked`,
`evidence-trial`}, and the third state exists for exactly this: "operator-initiated
evidence work", milestone-exempt while the trial runs, with the label held until a human
records the adopt/reject decision (release plan, "evidence-trial"; `AGENTS.md:13-15`). An
earlier draft of this document said "do not file yet" and "the honest label would be
`parked`"; that was wrong, because a sweep with no issue is invisible to every report and
leaves the trial without the adopt/reject record the label requires. The
pre-registration — arms, pool disjointness, decision rules, baselines — goes in the issue
body before the first run; the number goes in afterwards. This is decision D3 of the
[Categories Action Plan](https://github.com/fasrc/archi/blob/docs/categories-action-plan/docs/docs/proposals/categories-action-plan.md)
(PR #512).

**If rung 1/2 is ever merged ahead of its named release**, the plan's dark-ship rule
applies: on this single trunk, code merged to `dev` rides the next release regardless of
milestone, so a category filter or an exemplar injector must be an off-by-default toggle
with no user-visible surface until the release that claims it. That needs **dedicated
toggles whose code default is `false`** — one for the rung-1 surface (chunk header plus
schema hint), one for rung-2 filtered or classified retrieval — read the way
`categorization.enabled` is read (`base-config.yaml:519`, `processing.py:1078`: code
default `false`, turned on only by the FASRC config). `force_initial_retrieval`
(`fasrc_docs_agent.py:256`) is **not** the precedent: its code default is `true`, so a
toggle shaped like it ships the feature on. And rung 1's category header must not be
added unconditionally, as an earlier draft of this document implied; ungated, it is
user-visible in the next release.
