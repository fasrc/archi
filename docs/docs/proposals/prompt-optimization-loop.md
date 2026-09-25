# Autonomous Prompt Optimization Loop

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** September 2026
**Status:** Design proposal — no milestone proposed; the tracking issue is filed as `evidence-trial` before the first loop runs (see "Release-plan judgment")
**Baseline read:** `origin/dev` @ `92e1b576`, 2026-09-25
**Companion:** [Release Plan 2026](release-plan-2026.md) · [In-Context Learning and Query→Category Mapping](icl-and-query-category-mapping.md) · [Feature-Matrix Campaign](feature-matrix-campaign-2026.md) · [Interpreting Benchmark Results](../interpreting_benchmark_results.md)

---

## TL;DR

Let Claude improve the agent's system prompt on its own: read why answers failed,
change the prompt, re-measure, keep or revert, repeat. It stops when it finds a change
that improves accuracy **by a statistically significant margin**.

The loop itself is easy, and almost all of it already exists (`qa_arm.sh --sweep`,
`qa_prepare.sh`, `sweep_tools.py`, `compare_runs.py`). Three findings shape the
design more than the loop does:

1. **Ten questions cannot prove a significant improvement on their own.** Each question
   either passes or fails, and a 4/10 → 7/10 jump happens by chance about one time in
   four. So the ten are a **tuning set** the loop iterates on, and the significance
   verdict comes from a **holdout set** (the other 99 golden-set questions) that the
   optimizer never sees. The holdout also blocks the main failure mode: a model editing
   a prompt against ten known reference answers will start writing those answers into
   the prompt.
2. **Measure run-to-run noise before changing anything.** Nobody has measured it for
   this evaluator (`interpreting_benchmark_results.md` §"Procedure A" is documented as
   never run). Until that number exists, no improvement can be told apart from luck.
   This is step 0.
3. **Pick questions a prompt can actually fix.** A question fails either because search
   never found the facts (no prompt can fix that) or because the facts were retrieved
   and the answer left them out or got them wrong (a prompt can). The QA evaluator
   records each search's returned text per attempt (`src/evaluation/qa/tool_traces.py:22`),
   so the two can be told apart mechanically.

---

## 1. Why this is worth doing

Prompt changes have so far been tested as hand-written arms in a sweep: an operator
writes three to five variants, runs them overnight and reads a leaderboard. That finds
the best of the variants someone thought of. An automated loop can instead read the
**specific** missed facts, form a hypothesis about why the model dropped them, and test
it. That is closer to how a person debugs a prompt, but it can run ten or more
iterations a night.

The risk is the usual one for any optimizer: it tunes to the test. Most of this design
exists to keep the measurement honest.

---

## 2. What already exists

| Need | Existing piece | Where |
|---|---|---|
| Score one prompt against a question set, fact by fact | `archi eval qa` (prepare → run → score): the judge marks each reference fact ("atom") entailed / missing / contradicted; an answer passes only if every required atom is entailed | `src/cli/qa_eval.py`, `src/evaluation/qa/`, `docs/docs/evaluation.md` |
| Test a prompt with no redeploy | `--agent-spec <file.md>` reads the prompt file directly | `src/cli/qa_eval.py` |
| Grade every run against the *same* atoms | `qa_prepare.sh --sweep` extracts atoms once; each arm copies the snapshot | `scripts/benchmarking/feature_matrix/qa_prepare.sh` |
| Run one prompt arm on a locked stack | `qa_arm.sh --sweep <dir> --stack <name> --arm <stem>` | `scripts/benchmarking/feature_matrix/qa_arm.sh` |
| Refuse if any input drifted | `sweep_tools.py lock / verify / archive` hashes every input | `scripts/benchmarking/feature_matrix/sweep_tools.py` |
| Paired significance test | `compare_runs.py --qa-run DIR --primary ARM=completion`: exact McNemar with Holm correction; `--noise-runs` / `--noise-floor` for continuous metrics | `scripts/benchmarking/compare_runs.py`, `paired_tests.py` |
| Repeat each question | `archi eval qa --attempts N` | `src/cli/qa_eval.py` |
| Answer-side RAGAS metrics | `factual_correctness_recall`, `factual_correctness_precision`, `noise_sensitivity`, `answer_accuracy`, `response_groundedness` (added alongside this proposal) | `src/utils/benchmark_schema.py` |

One `archi evaluate` stack is ingested once (about 50–67 minutes); after that, every
prompt arm is just an `archi eval qa` run against that stack's database. Cost per arm
is dominated by answer time: about 48 s per question (`categories-action-plan.md:67`).

---

## 3. Design

### 3.1 Two question sets

| Set | Size | Who sees it | Used for |
|---|---|---|---|
| **Tuning** | 10 | the optimizer: questions, missed atoms, judge notes, retrieved text, answers | every iteration's keep/revert decision |
| **Holdout** | the other 99 | nobody during iteration; the driver only reports its pass count after a confirmation | the significance verdict |

**Why the holdout is the other 99 and not 30.** With an exact McNemar test on paired
pass/fail, a 30-question holdout needs about +7 net flips (+23 percentage points) to
reach p < 0.05. That is a larger gain than a prompt edit plausibly delivers. On 99
questions, about +9 net flips (+9 points) is enough, for example 12 questions fixed and
3 broken (two-sided p ≈ 0.035). The test counts both directions, so churn costs power:
14 fixed and 5 broken is also +9 but gives p ≈ 0.064. A confirmation run costs about
80 minutes at one attempt; it runs only when a candidate has already cleared the tuning
bar.

### 3.2 Choosing the ten

From the most recent baseline QA run on the current stack:

1. Take every question that **failed** (at least one required atom not entailed).
2. For each missed atom, ask the same atom judge whether the atom is entailed by the
   **retrieved text** (the concatenated `tool_calls[*].response` for that attempt)
   rather than by the answer. The judge already exists; only its input changes.
3. A question is **prompt-fixable** if every missed atom was present in what was
   retrieved. Rank these by how often they fail across attempts.
4. Tuning set = **7 prompt-fixable failures + 3 currently passing canaries.** The
   canaries catch regressions inside the loop, before a confirmation run is spent.
5. Record the selection and each question's classification in the campaign ledger.

A set built as "the ten hardest" would mostly be retrieval misses, and the loop would
spend its night on changes that cannot help.

### 3.3 Step 0: measure the noise

Run the **unchanged** production prompt three times on the tuning set (5 attempts per
question, pinned atoms), then once on the holdout. Record:

- per-question pass-rate spread across the three tuning runs (how often a question
  flips with nothing changed);
- sigma of the tuning-set mean atom score across the three runs. This is the noise
  floor the keep/revert rule uses;
- the holdout baseline pass count (the confirmation reference).

About 2 hours for the three tuning replicates (40 minutes each at one run worker)
plus about 80 minutes for the holdout: roughly 3 h 20 min in all.

### 3.4 One iteration

```
1. Read    failure dossier for the tuning set only:
           question · missed atoms · judge verdict per atom · retrieved text · answer
2. Propose ONE hypothesis and ONE prompt change that tests it, logged to the ledger:
           {iteration, hypothesis, targeted atoms/questions, diff}
3. Check   the spec loads (load_agent_spec) and passes the leak check (§3.6)
4. Run     qa_arm.sh --sweep <dir> --stack <name> --arm <stem>   (10 q × 5 attempts)
5. Decide  keep if  Δ mean atom score (tuning) > 2σ_noise
                and no canary lost more than 1 of its 5 attempts' passes
           else revert; log the result either way
```

About 40 minutes per iteration at one run worker. Ten iterations fit in a night.

The optimizer works from the **current best** prompt, so kept changes stack up. A
reverted hypothesis stays in the ledger so it is not retried under a new name.

### 3.5 Confirmation and stopping

When a kept prompt beats the baseline on the tuning set by more than 2σ, and at least
three iterations have passed since the last confirmation:

1. Run it on the holdout (1 attempt, pinned atoms).
2. `compare_runs.py --qa-run <baseline> --qa-run <candidate> --primary <arm>=completion`.
   Exact McNemar, Holm-corrected across every confirmation spent in this campaign.
3. **Significant (Holm p < 0.05) and positive:** stop and report.
   **Otherwise:** continue iterating. The holdout pass count is reported to the
   operator but not fed back to the optimizer.

**Hard stops:** at most **3 confirmations** per campaign (every extra look at the
holdout spends its statistical power, and Holm makes each look cost more), at most
**12 iterations**, or **4 consecutive reverts**.

**After a significant result**, one more step guards against the winner's curse (the
best of several tries tends to overstate its own gain): a fresh replicate of both
prompts on the holdout, plus a full RAGAS golden-set run with the answer-side metrics
enabled. The expected signature of a real prompt improvement is
`factual_correctness_recall` up, `noise_sensitivity` flat or down, and the `context_*`
metrics roughly flat. The agent writes its own search queries, so if the context
metrics move, the prompt changed retrieval too, and the report must say so.

### 3.6 Rules the optimizer must follow

- **One hypothesis per change**, written down before the run.
- **No reference content in the prompt.** A leak check compares each new prompt with
  the frozen atoms and reference answers of **all 109** questions and refuses the arm if
  new text shares any 6-word run, URL, path, or numeric value with them. Tokens
  already in the baseline prompt, and the docs site root
  (`docs.rc.fas.harvard.edu`), are allowlisted; otherwise any added link would trip
  it, since references cite that site. General
  guidance ("list every required module, not only the first") is allowed; facts ("the
  default partition is `shared`") are not.
- **Never see the holdout.** The driver gives the optimizer tuning-set dossiers only.
- **Prompt edits only.** No config, retrieval or code changes; those belong to the
  feature-matrix process.
- **Every arm locked.** `sweep_tools.py verify` must pass before and after each run.

### 3.7 Preconditions (checked by the driver before step 0)

- The stack's corpus fingerprint matches its pin (`qa_arm.sh` already refuses otherwise).
- The SUT vLLM endpoint named by the stack's config is up and **reserved for the
  campaign**. A shared endpoint changes latency, and a model swap changes everything.
- Host eval environment pinned: `transformers==4.57.6`, `sentence-transformers==5.1.2`.
  Without them every attempt silently fails with exit code 0.
- Judge key present (`HUIT_API_KEY_FILE`) and atoms prepared once (`qa_prepare.sh --sweep`).
- No RAGAS run and no other QA run on the same stack for the duration.

---

## 4. What gets built

Small and test-first. Everything in §2 is reused as is.

| Piece | Purpose |
|---|---|
| `scripts/benchmarking/prompt_opt/select_tuning_set.py` | §3.2: classify failures as retrieval vs generation using the atom judge on retrieved text; emit the 10-item QA dataset and the 99-item holdout |
| `scripts/benchmarking/prompt_opt/leak_check.py` | §3.6: refuse a prompt that shares 6-grams, URLs, paths or numbers with any reference |
| `scripts/benchmarking/prompt_opt/decide.py` | §3.4/§3.5: the keep/revert rule, the confirmation trigger and the stop rules, reading `summary.json` and the ledger |
| `scripts/benchmarking/prompt_opt/dossier.py` | §3.4 step 1: one failure dossier per tuning question from `evaluation_results.jsonl` |
| `scripts/benchmarking/prompt_opt/run_campaign.sh` | Driver: preconditions → step 0 → iterate → confirm, calling `qa_arm.sh --sweep` |
| Optimizer brief (skill `archi-prompt-optimize`) | The instructions the Claude session follows each iteration: read the dossier, write one hypothesis, edit the prompt, log it, never read the holdout |

The optimizer runs as a Claude Code session driven one iteration at a time (a `/loop`
or a systemd timer, like the nightly jobs). The deterministic parts (running, deciding,
leak checking) are scripts with unit tests; only "read the failures and propose an
edit" is the model's job.

**Campaign state** lives in `bench_out/banks/golden/prompt-opt/<campaign>/`:
`ledger.jsonl` (one line per iteration), `arms/<stem>.md`, the tuning and holdout
datasets, and each run's QA output. That is where the golden-set report already lives,
and it keeps archi itself free of run artifacts.

---

## 5. Cost

| Step | Time |
|---|---|
| Stack ingest (once, if no locked stack exists) | 50–67 min |
| Step 0: 3 tuning replicates + 1 holdout baseline | ~3 h 20 min |
| Each iteration (10 q × 5 attempts) | ~40 min |
| Each confirmation (99 q × 1 attempt) | ~80 min |
| A 12-iteration campaign with 3 confirmations (after step 0) | ~12 h (8 h iterating + 4 h confirming) |
| Whole campaign including step 0 | ~15–16 h, about two nights |

Judge cost scales with atoms judged. The five new RAGAS metrics are used only in the
final RAGAS confirmation, not in the loop.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Overfitting to ten questions | Holdout verdict; leak check; one hypothesis per change |
| Judge noise read as signal | Step-0 noise floor; pinned atoms; 5 attempts per tuning question |
| Too many looks at the holdout | Maximum 3 confirmations, Holm-corrected; holdout scores never reach the optimizer |
| Prompt quietly changes retrieval | Report `context_*` movement in the final RAGAS run |
| Gains that don't survive a replicate (winner's curse) | Fresh replicate of both prompts before adoption |
| Stack or SUT drift mid-campaign | `sweep_tools.py verify` around every run; corpus pin check in `qa_arm.sh` |
| The optimizer tunes the prompt for the judge rather than for users | Atoms are fixed facts, not style; a human reads the final diff before adoption |

---

## 7. Release-plan judgment

This is operator-driven evidence work: the output is a measured prompt change, not a
feature. Following the precedent set by the in-context-learning proposal, the tracking
issue is labelled `evidence-trial`, so it is milestone-exempt and nightly automation
never schedules or drains it. A winning prompt is adopted through the normal path: a PR
that changes the deployed agent spec, citing the campaign ledger and the `compare_runs`
verdict as evidence.

## 8. Decisions taken as defaults (operator may override)

- **Holdout size:** all 99 remaining questions (see §3.1 for why 30 is too small).
- **Win metric:** pass rate on the holdout decides (McNemar); the mean atom score is
  the tuning signal inside the loop; the answer-side RAGAS metrics are corroboration.
- **Tuning set composition:** 7 prompt-fixable failures + 3 passing canaries.
