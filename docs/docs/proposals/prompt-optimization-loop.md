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

The loop itself is easy, and most of it already exists (`archi eval qa`,
`qa_prepare.sh`, the `lib.sh` stack and corpus guards, the exact McNemar test in
`paired_tests.py`). The one piece that does not fit is the sweep runner: it runs a fixed,
pre-locked set of prompts at one attempt, so the loop gets its own runner (§3.4). Three findings shape the
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
| Grade every run against the *same* atoms | `archi eval qa prepare` extracts atoms once into a run directory; each run copies that snapshot (the pattern `qa_prepare.sh --sweep` and `qa_arm.sh --sweep` use) | `scripts/benchmarking/feature_matrix/qa_prepare.sh`, `qa_arm.sh:80` |
| Stack, corpus and code guards | `fm_require_stack_name`, `fm_require_pinned_corpus`, `fm_code_tree`, `fm_fingerprint`, `fm_ledger_append` | `scripts/benchmarking/feature_matrix/lib.sh` |
| Hash-and-verify inputs | `sweep_tools.py lock / verify` hashes every input of a **fixed** arm set | `scripts/benchmarking/feature_matrix/sweep_tools.py` |

**Not reusable as is: `qa_arm.sh --sweep`.** It hard-codes `--attempts 1`
(`qa_arm.sh:87`), and it runs only a prompt already in the sweep lock
(`qa_arm.sh:62`). The lock hashes every arm's prompt (`sweep_tools.py:224`), and the stack
is stamped with the sha256 of the lock file itself (`lib.sh:376`). A prompt written
mid-campaign is not in the lock, editing a locked prompt fails `verify`, and adding an
arm changes the lock's hash and breaks the stamp, which costs a redeploy. The loop
therefore has its own runner (§3.4, §4) built from the same `lib.sh` guards.
| Paired significance test | `paired_tests.paired_binary`: exact McNemar over two pass/fail maps keyed by question. `compare_runs.py` is **not** reused for the verdict: it needs benchmark artifacts as positional arms (this loop makes none), and its `completion` test pairs `status == "ok"`, not QA pass outcomes | `scripts/benchmarking/paired_tests.py:47` |
| Per-question QA pass outcome | `summary.json` → `items[].item_pass_rate` (1.0 or 0.0 at one attempt) | `archi eval qa` run directory |
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

**Why the holdout is the other 99 and not 30.** Each confirmation tests at
α = 0.05 / 3 ≈ 0.0167 (§3.5). With an exact McNemar test on paired pass/fail, a
30-question holdout needs at least +7 net flips (+23 percentage points) with no
question broken. That is a larger gain than a prompt edit plausibly delivers. On 99
questions, about +10 net flips (+10 points) is enough, for example 12 questions fixed
and 2 broken (two-sided p ≈ 0.013). The test counts both directions, so churn costs
power: 12 fixed and 3 broken is +9 and gives p ≈ 0.035, which passes at 0.05 but not
at 0.0167. A confirmation run costs about
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

Run the **unchanged** production prompt three times on the tuning set
(`run_arm.sh --set tuning --attempts 5`, pinned atoms), then once on the holdout
(`--set holdout --attempts 1`). Record:

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
4. Run     prompt_opt/run_arm.sh --campaign <dir> --prompt arms/<stem>.md
                                 --set tuning --attempts 5          (10 q × 5 attempts)
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

1. Run it on the holdout: `run_arm.sh --set holdout --attempts 1` (pinned atoms).
2. `holdout_test.py <baseline-run-dir> <candidate-run-dir> --look <k>` (§4). It pairs
   the two runs' `items[].item_pass_rate` by `item_id`, so a completed but wrong
   answer counts as a failure. Then it runs `paired_tests.paired_binary` (exact
   McNemar) and appends `{look, b, c, p}` to the campaign ledger. It refuses runs with
   more than one attempt, differing item sets, or a `k` that is not the next look in
   the ledger.
3. **Significant (p < 0.05 / 3) and positive:** stop and report.
   **Otherwise:** continue iterating. The holdout pass count is reported to the
   operator but not fed back to the optimizer.

**Multiplicity.** The alpha of 0.05 is split before the campaign starts: each of the
at most three looks tests at 0.05 / 3 (Bonferroni). The chance of any false positive
across the campaign is then at most 0.05, for any number of looks up to three and any
correlation between them, and a campaign that stops early spends no more. Holm is not
used across looks: it ranks p-values that all exist at the same time, and a campaign
that stops at a significant look never produces the later ones. The Holm correction
inside `compare_runs.py` covers only the secondary tests of one call.

**Hard stops:** at most **3 confirmations** per campaign (the per-look alpha is
fixed at 0.05 / 3, so a fourth look has no alpha left), at most **12 iterations**, or
**4 consecutive reverts**.

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
- **Every arm locked, append-only.** The campaign lock (§4) is written once, before
  step 0. It pins the code tree, the stack, the corpus, the judge profile, the baseline
  prompt, and the two prepared atom snapshots (tuning and holdout). It pins no
  candidate prompt, so it never changes and the stack stamp stays valid. Each
  candidate prompt is a new file `arms/<stem>.md`. The runner appends its sha256 to the
  ledger before the run and refuses a stem already in the ledger. After the run, it
  checks the file again and voids the run if the hash changed. A prompt file is never
  edited in place: the next change is the next stem.

### 3.7 Preconditions (checked by the driver before step 0)

- The stack's corpus fingerprint matches its pin (`fm_require_pinned_corpus`, the check
  `qa_arm.sh` already uses).
- The SUT vLLM endpoint named by the stack's config is up and **reserved for the
  campaign**. A shared endpoint changes latency, and a model swap changes everything.
- Host eval environment pinned: `transformers==4.57.6`, `sentence-transformers==5.1.2`.
  Without them every attempt silently fails with exit code 0.
- Judge key present (`HUIT_API_KEY_FILE`). Atoms prepared once per set: one
  `archi eval qa prepare` on the 10-item tuning dataset and one on the 99-item holdout
  dataset. The sets do not overlap, so each question's atoms are extracted exactly once.
  `archi eval qa run` has no item filter, and a prepared directory carries its item
  count in its manifest, so a full-bank snapshot cannot be cut down to a subset.
- No RAGAS run and no other QA run on the same stack for the duration.

---

## 4. What gets built

Small and test-first. Everything in §2 is reused as is.

| Piece | Purpose |
|---|---|
| `scripts/benchmarking/prompt_opt/select_tuning_set.py` | §3.2: classify failures as retrieval vs generation using the atom judge on retrieved text; emit the 10-item QA dataset and the 99-item holdout |
| `scripts/benchmarking/prompt_opt/leak_check.py` | §3.6: refuse a prompt that shares 6-grams, URLs, paths or numbers with any reference |
| `scripts/benchmarking/prompt_opt/holdout_test.py` | §3.5: paired exact McNemar over two holdout QA runs' per-item pass outcomes, at the per-look alpha 0.05 / 3; each look appended to the ledger |
| `scripts/benchmarking/prompt_opt/decide.py` | §3.4/§3.5: the keep/revert rule, the confirmation trigger and the stop rules, reading `summary.json` and the ledger |
| `scripts/benchmarking/prompt_opt/dossier.py` | §3.4 step 1: one failure dossier per tuning question from `evaluation_results.jsonl` |
| `scripts/benchmarking/prompt_opt/lock_campaign.py` | §3.6: write the campaign lock once (code tree, stack, corpus pin, judge profile, baseline prompt, both prepared snapshots); `verify` re-hashes all of it |
| `scripts/benchmarking/prompt_opt/run_arm.sh` | §3.4: one QA run of one prompt file: `--campaign <dir> --prompt <file> --set tuning\|holdout --attempts N`. It uses the `lib.sh` guards as `qa_arm.sh --sweep` does, verifies the campaign lock, copies the chosen prepared snapshot, and passes `--attempts N` to `archi eval qa run`. It also applies the append-only prompt rule of §3.6 |
| `scripts/benchmarking/prompt_opt/run_campaign.sh` | Driver: preconditions → step 0 → iterate → confirm, calling `run_arm.sh` |
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
| Too many looks at the holdout | Maximum 3 confirmations, each at the preallocated alpha 0.05 / 3; holdout scores never reach the optimizer |
| Prompt quietly changes retrieval | Report `context_*` movement in the final RAGAS run |
| Gains that don't survive a replicate (winner's curse) | Fresh replicate of both prompts before adoption |
| Stack or SUT drift mid-campaign | Campaign-lock `verify` around every run; corpus pin check before and after each run in `run_arm.sh` |
| A prompt changes after its run | Append-only prompt files; hash in the ledger before the run, checked again after it |
| The optimizer tunes the prompt for the judge rather than for users | Atoms are fixed facts, not style; a human reads the final diff before adoption |

---

## 7. Release-plan judgment

This is operator-driven evidence work: the output is a measured prompt change, not a
feature. Following the precedent set by the in-context-learning proposal, the tracking
issue is labelled `evidence-trial`, so it is milestone-exempt and nightly automation
never schedules or drains it. A winning prompt is adopted through the normal path: a PR
that changes the deployed agent spec, citing the campaign ledger and the `holdout_test.py`
verdict as evidence.

## 8. Decisions taken as defaults (operator may override)

- **Holdout size:** all 99 remaining questions (see §3.1 for why 30 is too small).
- **Win metric:** pass rate on the holdout decides (McNemar); the mean atom score is
  the tuning signal inside the loop; the answer-side RAGAS metrics are corroboration.
- **Tuning set composition:** 7 prompt-fixable failures + 3 passing canaries.
