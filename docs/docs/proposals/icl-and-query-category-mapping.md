# In-Context Learning and Query→Category Mapping

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** September 2026
**Status:** Exploration — no issue filed, no milestone proposed
**Baseline read:** `origin/dev` @ `4b253e2`, 2026-09-19
**Companion:** [Release Plan 2026](release-plan-2026.md) · [Multi-Collection Routing](multi-collection-routing.md) · [Feature-Matrix Campaign](feature-matrix-campaign-2026.md)

---

## TL;DR

Two ideas were raised:

1. **In-context learning (ICL)** — put worked examples in front of the model.
2. **Query→category mapping** — have the agent map the query to a category in
   `docs.rc.fas.harvard.edu` before answering.

Both are **prompt-only at rung 0** and need **no code at all** to measure, because
`generate_prompt_sweep.py` already A/B's agent prompts with everything else held fixed.

The load-bearing point for idea 2 is that it is **half-built and inert** — already
established by [#496](https://github.com/fasrc/archi/issues/496) ("the label is
write-only … readers: none"), and confirmed here for the breadcrumb field too: the FASRC
docs category is captured at ingest into `metadata["category"]`
(`processing.py:181-208`), and **nothing on the retrieval or prompt path ever reads it**
— not the retriever, not the chunk header the model sees, not the schema hint tool. That
inertness is the mechanism behind the campaign's arm-03 result
([#496](https://github.com/fasrc/archi/issues/496): 19.2 min per ingest, no measurable
retrieval effect) — a writer was enabled with no reader, so "no effect" was the only
possible outcome. **Arm 03 is evidence against the ingest-side label as wired. It is not
evidence against mapping the query to a category.**

Neither idea clears the release plan's gate bar today. Rung 0 runs on the
**prompt-sweep harness**, which is the vehicle the feature-matrix campaign
([#396](https://github.com/fasrc/archi/issues/396)) explicitly points at for this: that
campaign puts prompt variants **out of scope** (§11) and pins the agent prompt as a fixed
factor by sha256 (§2), so a prompt arm is not an arm of it and needs its own
pre-registration. What #396 does supply is everything that makes the arm measurable — its
power numbers, its baselines, `compare_runs.py`, and the arm-03 result above.

---

## What exists today

### The category is captured, and nothing reads it

| Stage | Where | State |
|---|---|---|
| Breadcrumb → `metadata["category"]` | `processing.py:181-208` (`HtmlCategoryProcessor`) | **live** — default on, runs whenever `html_to_markdown.enabled` (`processing.py:1072-1076`) |
| LLM label → `metadata["llm_category"]` | `processing.py:857` (`CategorizationProcessor`) | **live on FASRC**, code-default `false` (`base-config.yaml:502`) |
| Catalog persistence | `catalog_postgres.py:295` → `extra_json` / `extra_text` | **live** — `_build_extra_text` (`:1560-1569`) emits `category:<value>` |
| Chunk metadata | `manager.py:484,574` (`file_level_metadata` merged into every chunk) | **live** — the field reaches `document_chunks` |
| Retrieval filter | `HybridRetriever._get_relevant_documents` (`hybrid_retriever.py:63-116`) | **absent** — calls `hybrid_search(query, k, weights)` and forwards no `filter`, though `PostgresVectorStore.hybrid_search` accepts one (`postgres_vectorstore.py:439,449`) |
| What the model is shown | `_format_documents_for_llm` (`tools/retriever.py:78-98`) | **absent** — header is `[i] title <url> (hash=…)` + `Score:`; no category |
| Schema hint to the model | `api_catalog_schema` (`uploader_app/app.py:779-790`) | **absent** — `keys` is `sorted(_METADATA_COLUMN_MAP.keys())` (`catalog_postgres.py:49-66`), and `get_distinct_metadata` allows only `source_type`, `suffix`, `ticket_id`, `git_repo`, `url` (`:554-564`). `category` is in neither |
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

The bar comes from the campaign's own decision rules, as recorded on
[#496](https://github.com/fasrc/archi/issues/496) for arm 03:

- RAGAS deltas are verdicts only outside `MDE = max(2·SE, 2σ)` — **measured at 0.025–0.05**
  per metric on this bank, in both runs. Mean deltas inside that spread need roughly
  **40 runs per arm** to carry a claim.
- Gold-atom deltas only outside 2σ of the four-run floor: pass `0.424 ± 0.024`,
  atom `0.475 ± 0.017`, required-atom recall `0.614 ± 0.010`.
- Count-type metrics were decisive far cheaper: source accuracy `0.868` (McNemar),
  recursion-limit blowouts `7 / 109`, time per question `48.2 s`.

**Consequence for both ideas: judge them on the count-type metrics — source accuracy,
blowout count, required-atom recall — not on RAGAS means.** A two-run RAGAS arm cannot
resolve a prompt effect of plausible size, and reporting one would be the kind of
overclaim `v2026.09.0` exists to prevent.

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
measures 8% of FASRC questions ending in a recursion-limit blowout at ~230 s, and the
in-loop bound spends a 15% safety margin (`base_react.py:1696-1697`) over a
4-chars-per-token estimate (#263, parked). Static exemplars add fixed tokens to every
turn on top of that. The blowout count is already a campaign metric (`7 / 109`), so the
interaction is measurable in the same arm — but it must be **read**, not assumed benign.

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
   then search — the category shapes the query text, nothing is excluded. No new failure
   mode; also the only variant that is free.
2. **Show the category (rung 1).** Add `category` to the chunk header in
   `_format_documents_for_llm`, so the model can see which section each hit came from and
   reject off-category hits itself. One field in one f-string, plus a test.
3. **Filter with fallback (rung 2).** Plumb `filter` through `HybridRetriever` into
   `hybrid_search`, run filtered, and re-run unfiltered when the filtered set is empty or
   thin. Never a bare `WHERE`.

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
2. **Vocabulary agreement.** Do the 20 LLM labels in the FASRC config match the
   breadcrumb vocabulary? [#496](https://github.com/fasrc/archi/issues/496) notes the
   labels "duplicate the breadcrumb `category` field the scraper already writes" — if the
   two vocabularies disagree, a prompt that hard-codes one will filter against the other.

The taxonomy in any rung-0 prompt must be **copied from the host, not from this
document**: `docs.rc.fas.harvard.edu` was unreachable from the session that wrote this,
and the 20-label list lives in `fasrc/archi-config` `config/environments/dev.yaml:292-301`.

---

## Staging

**Rung 0 — no code, measurable now.** Two prompt files through
`generate_prompt_sweep.py`:

- **(a) category-mapping prompt** — name the docs category before searching; optionally
  advertise the working `category:<value>` filter on `search_metadata_index`, with its
  substring caveat stated in the prompt.
- **(b) static-ICL prompt** — 3–5 exemplars from a pool disjoint from the 109-question
  bank, with the disjointness recorded.

Read source accuracy (McNemar), required-atom recall, blowout count and time per
question against the campaign baselines. Do **not** read RAGAS means at two runs.

**Rung 1 — small, only if rung 0 shows signal.** Surface `category` in
`_format_documents_for_llm` (`tools/retriever.py:94`), and teach
`api_catalog_schema`/`get_distinct_metadata` to offer `category` and its distinct values
so the model can discover the vocabulary instead of carrying it in the prompt. Note the
second is not one line: `category` is not a promoted column, so distinct values need a
JSONB query over `extra_json` or a schema migration.

**Rung 2 — a feature, with its own issue.** `filter` plumbed through `HybridRetriever`
into `hybrid_search`; a query-classification step at the `_inject_forced_retrieval` seam;
fallback-on-empty. Dynamic ICL joins here, sharing the same seam and the same exemplar
store. Both overlap [Multi-Collection Routing](multi-collection-routing.md) — a category
filter and a collection filter are the same mechanism at different granularity, and the
two should not grow separate vocabularies.

Rung 1 touches `tools/retriever.py` and `uploader_app/app.py`. Neither is `app.py`, so
the diff-cover trap in `CLAUDE.md` does not apply, but both are large files — run the
`black-seam-scout` check before editing in place.

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
0 is a standalone sweep with its own pre-registration, reusing the campaign's baselines and
decision rules. Those rules still do the rest: a *no measurable difference* on a free
prompt change is a finding that closes the question, and a positive count-metric result is
the evidence a rung-1/2 issue would need to enter a milestone honestly.

Because it is its own sweep rather than a campaign arm, the house pattern for recording it
is a separate issue — which is how #496, #497 and #498 were filed out of the campaign —
not a comment on #396.

**Do not file an issue yet.** The plan's invariant is that every open issue carries
exactly one of {a milestone, `parked`, `evidence-trial`} — so filing before there is a
number forces a scheduling decision nobody is in a position to make, and the honest
label would be `parked` on day one. File after rung 0, with the number in the body.

**If rung 1/2 is ever merged ahead of its named release**, the plan's dark-ship rule
applies: on this single trunk, code merged to `dev` rides the next release regardless of
milestone, so a category filter or an exemplar injector must be an off-by-default toggle
with no user-visible surface until the release that claims it. Both rungs are naturally
toggleable — `force_initial_retrieval`
(`fasrc_docs_agent.py:256`) is the precedent for exactly this shape.
