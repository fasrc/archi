# Pre-registration: `2026-09-feature-matrix`

> **Lock this file (commit it to the benchmarking branch) BEFORE running the eval.** The whole point of pre-reg is to time-stamp the decision rule so we can't quietly cherry-pick the metric that flatters the preferred config after the fact.

Campaign plan: [`docs/docs/proposals/feature-matrix-campaign-2026.md`](../../docs/proposals/feature-matrix-campaign-2026.md)
(issue [#396](https://github.com/fasrc/archi/issues/396)). This file fixes the decision
rules; the plan holds the protocol.

## Round metadata

| Field | Value |
|---|---|
| Round ID | `2026-09-feature-matrix` |
| Operator | Austin Swinney |
| Host | `holygpu7c0717` (`archi.rc.fas.harvard.edu`), the production `dev` GPU host. Deviation from the plan's original claw pin, decided 2026-09-04 before the lock; recorded because benchmark artifacts do not carry the host ([#433](https://github.com/fasrc/archi/issues/433)) |
| Date locked | **2026-09-04** (smoke green on every wrapper, then re-locked on the campaign inputs) |
| Commit at lock | `93e0904d8ef82a4531931dbde0b8dc6df0fe7f77` on `bench/2026-09-feature-matrix`. This is a branch SHA, not a `dev` SHA: the branch carries only docs commits on top of `dev` tip `3170498c`, and the invariant that matters is that the locked runtime trees are identical to `3170498c`'s (`src=3911adee`, `scripts=dc42dfbc`, `deploy=8ee0ac2c`, `pyproject.toml=c95c72d5`, `requirements=c451e960`). Campaign lock sha256 `3e07ae79734682c682caf7e70704eeb247ba2f13313ed56e673dd6de62c65ea8` |
| Question bank | `config/benchmarking/fasrc_ragas_queries.json` (archi-config), 105 rows, all `status: draft`; git blob `99efd5b4d4f37dc696476be0f82113251987dc45`, sha256 `a116bd724d6f8ef99aeac96ff100ea63f72ee109dd048ffe86ebbe0dbffd4156` |
| Anchors | `examples/benchmarking/anchor_questions.json`, 5 rows, sha256 `6b2fd99175b26011336a3159d23fa6991c90cfe51d1110067fbb890f8e80311e`; one `should_refuse` anchor duplicates a bank row → 109 questions asked |
| Agent prompt | `config/agents/claw/fasrc-docs.md`, sha256 `ac22702ae6767c49ae7fdc65fb4c40211d97724c574585d1e321468377d64ce8`, byte-identical to the GPU host's `deploy/fasrc-dev/agents/fasrc-docs.md`. **This was made true on 2026-09-04, not merely asserted:** the host served `fasrc-v2.md` (name `FASRC`, `search_vectorstore_hybrid` only) until that date, so the claim was false when this file was drafted. Production was migrated to this spec and the old one retired to `agents/archive/`; verified by a live cited chat answer. Consequence: the two extra tools (`search_local_files`, `search_metadata_index`) are now live in production and ride the still-open [#139](https://github.com/fasrc/archi/issues/139) context-budget gap — watch the degraded-row count |
| Sources | `config/lists/sources.list`, sha256 `8d4590f4841a640f8bf32c413bc5cc189db338e4019cdcdbf795b633e0cb787f` |
| Configs compared | `config/benchmarking/feature_matrix/00-baseline.yaml` vs each of `01-rerank-off`, `02-chunking-character`, `03-categorization-off`, `04-stemming-on`, `05a-k3`, `05b-k8`, `06-html-to-markdown-off`, `07-chunking-markdown` |
| SUT | vLLM `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4`, `temperature: 0.3`, `enable_thinking: false`, in-loop context bound `context_windows: 32768` / `keep: 1`. That bound was **absent from every arm YAML until 2026-09-04** and was added pre-lock so the arms match production; before the fix the lock recorded `sut.context_editing = null` and the runtime warned it had no bound. This matters because `fasrc-docs.md` selects `search_metadata_index`, which auto-includes `fetch_catalog_document`, whose `max_chars` is still unclamped under open [#139](https://github.com/fasrc/archi/issues/139) |
| Judge | `huit_bedrock / us.anthropic.claude-sonnet-4-5-20250929-v1:0` for both RAGAS and the QA atoms evaluator |
| Evaluators (target N) | model judges only; no human grading round in this campaign |

## Primary hypothesis

For each toggle, the arm that flips it changes retrieval quality relative to the shipped
default in a direction and size the campaign can detect: a paired per-question delta on
the primary metric larger than both twice its standard error and twice the run-to-run
noise of the mean. The null (kept if the delta is smaller) is "no measurable difference at
N = 2".

Stated per arm, as the falsifiable claim the arm tests:

| Arm | Claim under test |
|---|---|
| 01 rerank off | Disabling the hierarchical rerank lowers `context_precision` (the ADR 0003 "+19 % RAGAS" still holds on sentence chunking). |
| 02 character chunking | Flat character chunks + hybrid retriever lower `context_precision` versus sentence parent/child chunking. |
| 03 categorization off | Removing `llm_category` does not change `context_precision` (the per-document LLM call buys nothing measurable). |
| 04 stemming on | Stemming the indexed text raises `context_recall` through BM25. |
| 05a k = 3 / 05b k = 8 | Fewer parents raise `context_precision` and lower `context_recall`; more parents do the reverse. |
| 06 html_to_markdown off | Raw-HTML text lowers `context_precision` and `faithfulness`. |
| 07 markdown chunking | Header-aware chunking raises `context_precision` on the Markdown-bearing part of the corpus. |

## Primary outcome (the metric that decides)

- **Metric:** `context_precision` for every arm except **04**, whose primary is
  `context_recall`.
- **Computed how:** paired per-question delta (arm − baseline) on rows with `status == ok`
  and a finite score in both arms, joined on question text; the five tripwire anchors
  excluded from the bank aggregate; both arms' N runs pooled. Computed by
  `scripts/benchmarking/compare_runs.py` (#419), never by hand.
- **Why question text is a valid key:** the harness dedupes the bank and the anchors on
  exact text before asking, so an artifact holds 109 unique questions (the bank's 105
  texts are themselves unique). The tool refuses an artifact with a repeated text. The
  one anchor that duplicates a bank row is treated as the anchor: it sits in the anchor
  block and outside the bank aggregate, which therefore covers 104 rows. The QA family
  joins by the content-derived item id (question + reference), the same id the converter
  assigns.
- **Source data:** the arm's `bench_out/feature_matrix/*.json` artifacts named in
  `ledger.json`; σ from the three opening-baseline runs (`--noise-runs`).

## Decision rule

MDE = `max(2·SE_pooled, 2·σ_pooled)` for the primary metric, printed by the tool.

| Outcome | Threshold | Action |
|---|---|---|
| **helps** | Δ_primary > +MDE, and no G8 regression (below) | file an issue to change the shipped default, or record that the default is confirmed |
| **hurts** | Δ_primary < −MDE | file an issue against the default if the arm is the shipped setting; otherwise record "default confirmed" |
| **no measurable difference** | −MDE ≤ Δ ≤ +MDE | record with the MDE; if the setting costs ingest time or latency, file an issue proposing the cheaper setting |
| **mixed** | Δ_primary > +MDE but another RAGAS metric, `source_accuracy`, or the QA pass rate drops by more than one σ, or an `easy_retrieve` anchor drops by more than σ, or the `should_refuse` anchor fails | no default change; record the trade-off |

**Where σ comes from.** The MDE is computed from the three opening baseline runs of this
campaign (same prompt, corpus and code as every arm), never from earlier runs. The six
2026-08 same-code runs used a different prompt (`fasrc-inline-v1.md`) and serve only as
the planning prior: MDE ≈ 0.03 on `context_precision` / `context_recall` /
`answer_correctness`, ≈ 0.05 on `answer_relevancy` / `faithfulness`. **Pre-committed
rule:** if the opening σ on the primary metric exceeds that prior by more than 50 %, the
operator decides — before any other arm runs — whether to raise N for every arm, and
re-locks this file with the decision. A true effect below the locked MDE is out of reach
and is reported as the null, not as a direction.

## Secondary analyses (planned)

- [ ] All five RAGAS metrics and `source_accuracy`, paired, with MDE, for every arm.
- [ ] QA atoms family per arm: pass rate, atom score, required-atom recall, paired by item id; McNemar on pass/fail.
- [ ] Time to result: mean and p90 `time_elapsed` (harness) and `duration_ms` (QA), warm variants dropping the first question.
- [ ] Time to ingest (`ingest_wall_seconds`) and index size (`document_chunks` count) per ingest arm — the cost side of every verdict.
- [ ] Anchor block: `easy_retrieve` alarm, `reasoning` trend, `should_refuse` pass/fail.
- [ ] Slices by `anchor_type` (the bank carries `easy_retrieve` 29 / `reasoning` 73 / `should_refuse` 3).
- [ ] Degraded-row count per arm (context overflow), paired.
- [ ] Opening vs closing baseline: corpus fingerprint, bank blob hash, and metric drift.

## Stopping rule

- **Runs per arm:** N = 2 RAGAS runs and 1 QA run; baseline 3 opening + 1 closing RAGAS
  runs and 1 + 1 QA runs. Fixed in advance; no arm gets extra runs because its first
  result looked promising.
- **Void arm:** an arm whose corpus fingerprint, question set, code digest, or
  `divergence_from_selected_file` check fails is rerun once; a second failure is recorded
  as "failed, reason" and the arm carries no verdict.
- **Hard deadline:** none by date; the campaign ends when every arm has its runs or its
  failure record. Partial results are never reported as a matrix.

## Known voice/blinding caveats

- [x] The judge grades machine output only; no human blinding applies.
- [x] The same model judges both families, so a RAGAS-vs-atoms disagreement is a method
  difference, not a grader difference.
- [x] All 105 bank references are `draft`; the atoms family measures relative movement on
  unlocked references, not absolute quality.
- [x] The SUT samples at `temperature 0.3` (Gap 4 of the interpreting-results page): both
  arms carry that noise; it is inside σ.
- [x] Latency depends on shared vLLM load; runs are timestamped and compared within the
  same hours where possible.

## Out of scope for this round

- `chunking.chunk_overlap` — no config key until #403; sweep 20/64/128 in a later round.
- Embedding models — #216's axis; its winner becomes a later baseline.
- Interaction effects between toggles — one factor at a time only; the report states that
  the arms are not independent samples of a factorial design.
- Prompt variants — the prompt is fixed at the production spec for every arm.
- Judge changes mid-campaign — the pinned Bedrock model is used for every run.

## Outcome (filled after the campaign)

_Verdict per arm from the plan's ledger (§9), with the MDE beside each delta, the
opening-vs-closing drift check, and the follow-up issues filed._
