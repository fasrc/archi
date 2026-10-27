# Pre-registration: `2026-10-free-faithfulness-judge`

> **Lock this file (commit it) BEFORE scoring any judge on the test material.** The whole point of pre-reg is to time-stamp the decision rule so we can't quietly cherry-pick the metric that flatters the preferred judge after the fact.

Related: [#583](https://github.com/fasrc/archi/issues/583) (Lynx faithfulness metric, closed),
[#737](https://github.com/fasrc/archi/issues/737) (judge-offload and Lynx cluster steps; step 5 is
the judge comparison against Sonnet), [#773](https://github.com/fasrc/archi/issues/773) (Lynx token
limit setting). Prior evidence: `/scratch/a2rchi/bench-runs/lynx-vs-ragas/v11/RESULTS.md`.

## Why

Every golden-set run grades archi's answers with Claude Sonnet 4.5 through HUIT Bedrock. That
costs money per run (about $28 a run for all metrics, list price) and throttles. The operator
wants evidence that a judge with no per-token bill, or close to it, can check **faithfulness**
(is what the answer says backed by the documents archi retrieved) as reliably as the paid judge
does. Faithfulness only: correctness, relevancy and retrieval metrics are out of scope.

The operator reads benchmark results by looking for any number that moves up. With about a dozen
RAGAS numbers and normal run-to-run wobble, one of them usually rises by chance. So the job a
faithfulness judge must do is: catch the answers that state something wrong, and not cry wolf on
answers that are fine.

## What is already settled (not re-tested here)

Measured 2026-10-24 on the five rebuilt runs below (`RESULTS.md`):

- **Lynx 8B v1.1** (32,000-token window) passes 98.6% of 423 real answers, and its agreement with
  RAGAS is chance level (kappa ≈ 0). It also passed answers written for unrelated questions
  (9 of 10 short rows, 4 of 10 long rows, with no shared documents), and its reasoning describes
  the answer the documents imply, not the answer it was given. Reproduced in plain Hugging Face
  transformers, so it is the model, not this host's vLLM build. Our prompt matches the v1.1 model
  card, sent as one user message as the card says. **But** that control reused one donor answer
  (question_109's) in 13 of 20 items, 9 of the 10 short ones, so the evidence is narrower than it
  looks. v1.1 stays in as a candidate (L1) and is re-tested on the rebuilt control (C).
- **Lynx 8B v1.0** (8,000-token window) failed every unrelated answer it could read (3 of 3) and
  caught one real invented claim, but reads only 69 of 425 questions (16%). Lynx 70B has the
  same 8,000-token window.
- RAGAS faithfulness (Sonnet) scores a fraction of supported claims. Only 8.7% of real answers
  score 1.0; the mean is 0.68.

## The grading rule (operator decision, 2026-10-27)

> **Only made-up or wrong specifics count as a faithfulness failure.** A specific is a concrete
> fact a user could act on: a number, limit, path, hostname, partition, command, flag or option,
> software name or version, date, price, or a policy (who may do what). A specific fails when the
> documents contradict it, or when the documents do not contain it. General advice that is not in
> the documents ("check with your PI", "test on a small job first", "see the docs for updates")
> does **not** count as a failure. Paraphrase does not count. Leaving something out does not
> count.

This rule is the truth standard for the human grades and for the planted-error set. It is used
verbatim in the Argilla guidelines and in the rule-prompt judges (mode b below).

Consequence, stated up front: RAGAS faithfulness counts every unsupported claim, including general
advice, so it measures something broader than this rule. Neither RAGAS nor Lynx is the truth
here; the human grades and the planted errors are.

## Round metadata

| Field | Value |
|---|---|
| Round ID | `2026-10-free-faithfulness-judge` |
| Operator | Austin Swinney |
| Date locked | `<YYYY-MM-DD>` |
| Commit at lock | `<sha>` |
| Source runs | Golden-set runs of 2026-10-12 on corpus `…a67f2ac54f8` (7,006 chunks): retrieval A/B `012746`, `054355` (new code), `034614`, `081227` (old code); `130451` (new code, Qwen3.8) |
| Real answers | 425 rebuilt rows (every passage = the full parent text RAGAS judged; 120 of 545 questions left out because a source had no parent passage). Bundles: `/scratch/a2rchi/bench-runs/lynx-vs-ragas/runs/*/judge_bundles/*/rows.jsonl`, sha256 `64d46793…`, `7a1f263c…`, `809ddd0d…`, `b2574494…`, `dfc5b83b…` (full digests at lock) |
| Existing scores | RAGAS faithfulness per row from the original runs (`rebuild.jsonl`); Lynx v1.1 (`v11/out/`), Lynx v1.0 (`v10/out/`); joined in `v11/per_question.csv` |
| Incumbent judge | RAGAS `faithfulness`, Claude Sonnet 4.5 via HUIT Bedrock (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`; confirm against the original result files at lock), same `ragas` package version as the October runs |
| Human grader | Austin Swinney (Argilla user `aswinney`), single grader |

## Judges under test

| ID | Judge | How it runs | Cost | Verdict shape |
|---|---|---|---|---|
| S | **Incumbent:** RAGAS faithfulness, Sonnet 4.5 | HUIT Bedrock | paid | 0–1 score |
| L0 | Lynx 8B v1.0, rev `5523f869eb5eecb4bca417b6e6d3532e746a9664`, 8,000 tokens | local V100, existing scorer CLI | $0 | PASS/FAIL; reads short rows only |
| L1 | Lynx 8B v1.1, rev `00de9f9062d93a95e4e359d3c92b8ea1d0114738`, 32,000 tokens | local V100, existing scorer CLI | $0 | PASS/FAIL; real-row verdicts already exist |
| Ga | RAGAS faithfulness prompt, `openai/gpt-oss-120b` | OpenRouter | cents/run | 0–1 score |
| Na | RAGAS faithfulness prompt, `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter free tier | $0 | 0–1 score |
| Ma | RAGAS faithfulness prompt, `meta-llama/llama-3.3-70b-instruct` | OpenRouter | cents/run | 0–1 score |
| Gb | Rule prompt, `openai/gpt-oss-120b` | OpenRouter | cents/run | PASS/FAIL |
| Nb | Rule prompt, Nemotron 3 Ultra free | OpenRouter free tier | $0 | PASS/FAIL |
| Mb | Rule prompt, Llama 3.3 70B | OpenRouter | cents/run | PASS/FAIL |

- **Mode a** asks "does RAGAS's own faithfulness check give the same answers with a cheaper
  model?" It keeps the golden-set history comparable.
- **Mode b** asks "does a judge told the operator's rule measure what the operator cares about?"
- gpt-oss-120b and Llama 3.3 70B are open weights, so a judge that passes here can later run on
  the FASRC cluster with no token bill (the #729 path). OpenRouter is a stand-in for that only if
  the precision matches, so each OpenRouter model is pinned to one full-precision host (below).

## Test material

Every item has the bundle row shape (`key`, `user_input`, `retrieved_contexts`, `response`,
`reference`) so every judge reads identical input. New items are written as extra judge bundles
(`rows.jsonl` + `manifest.json`) so the existing Lynx scorer reads them unchanged.

### A. Real answers (425 rows)

All judges score all 425. The **decision** uses only the 40 human-graded rows (D); the rest feed
secondary analyses.

### B. Planted-error set (70 items, by construction)

Base rows: real rows with RAGAS faithfulness ≥ 0.8 **that also pass a cleanliness check**: every
specific in the answer (numbers, paths, hostnames, partition names, `code` spans, command-line
flags, quoted commands) occurs in the documents (case-insensitive; the builder fixes the exact
extraction and lists exclusions such as step numbers). Pool before the check: 155 rows; 28 short
enough for L0, covering only **14** distinct questions (127 long rows, 49 questions). If the clean
short pool runs out, fall back to short rows with RAGAS ≥ 0.5 that pass the check (15 questions).
Short targets are 20 harmful and 15 benign, with a floor of 10 and 8; any shortfall is filled from
long rows so the totals stay 40 and 30. Record the achieved split. One base row may carry one
harmful and one benign variant, as separate items. Seeded selection (`seed 20261027`), preferring
distinct questions, and no planted item built on a question used in D's real rows.

**Truth labels for B come from the construction**, not from any grader: harmful = should flag,
benign = should not. If the human grades a hidden planted item differently, that is reported as
a human miss or human false alarm, never a relabel.

**Who writes the edits (builder decision, to avoid tilting the comparison toward any judge's
model family):** harmful types 1–4 are mechanical one-token substitutions (another number;
another real-looking path, host or partition absent from the documents; another flag; inserting
or removing a negation). Type 5 (invented step) and the benign general-advice sentences come from
fixed lists written once and committed with the lock. Only the 15 paraphrases are written by a
model (Claude, the same family as S); that confound is accepted and visible in the per-type
results.

- **Harmful — 40 items, should be flagged.** One specific in one sentence is changed so the
  documents contradict it or do not contain it. 8 of each type: (1) number or limit,
  (2) path, hostname or partition, (3) command, flag or option, (4) policy flipped (allowed ↔ not
  allowed, required ↔ optional), (5) invented step or command. 20 short (fit L0), 20 long.
- **Benign — 30 items, should not be flagged.** 15 paraphrases of a sentence (same facts),
  15 with one sentence of general advice added that is not in the documents and contains no
  specific. 15 short, 15 long.

Edits must read naturally (no judge should be able to spot them by oddity), must keep a short
row under L0's limit, and are verified mechanically: a harmful edit's new specific does not occur
in the documents; a benign edit introduces no new specific. Every edit is recorded in a ledger
(base key, type, original sentence, edited sentence, verification result).

Each judge also has the base row's original verdict (from A), so catches can be read paired:
"flagged the planted version and not the original".

### C. Unrelated-answer control (20 items)

Rebuilt, so the gate tests 20 independent things: 20 rows from run `012746`, each given the
answer written for a **different** donor question that shares no parent passage with it; 20
distinct donors; 10 short (still within L0's limit after the swap) and 10 long; seeded. Every
one should be flagged. L0 scores the short ones it can read. The earlier far-swap set
(`v11/swap_far_v11.json`, 4 donors, question_109's in 13 of 20) is reported alongside for
comparability with the recorded Lynx results, but does not gate.

### D. Human-graded sample (46 items, in Argilla)

- **40 real rows**, seeded and stratified by incumbent score: 16 with RAGAS < 0.5, 12 with
  0.5–0.8, 12 with ≥ 0.8; half short where the stratum allows; at most one row per question.
- **6 hidden planted items** (3 harmful, 3 benign) drawn from B. They check that the human
  catches planted errors; they also count in B.
- Shuffled. The grader sees the question, the answer and the documents; never a judge's score or
  verdict, the run, the code version or the model.
- Argilla dataset `free-faithfulness-judge-2026-10` in workspace `archi`, assigned to `aswinney`.
  Guidelines = the grading rule above, verbatim, with two worked examples (one failure, one
  general-advice pass).
- Questions per record:
  1. *Does the answer state any made-up or wrong specific?* — No / Yes / Can't tell (required).
  2. *Paste the first made-up or wrong specific* — text (required when Yes).
  3. *How much of the answer's specific content do the documents back?* — all / most / about
     half / little (optional).
- Expected time: about 3–4 hours, in as many sittings as wanted. Browser find (Ctrl+F) on the
  documents is the intended way to check each specific.

### Prompt development split (excluded from all results)

The mode-b prompt may be tuned only on a dev split: 10 real rows from run `130451` not used in D,
plus 4 extra planted items (2 harmful, 2 benign) not used in B. The builder labels the 10 real
dev rows by the rule text (no extra operator time); D then measures the prompt against the
operator's own reading. The prompt is then frozen and its sha256 recorded before any test item is
scored. Mode a uses RAGAS's own prompt untouched. No judge sees the reference answer.

## Definitions

- **Flag.** Binary judges flag on FAIL. Score judges (S, Ga, Na, Ma) are read at two cutoffs
  declared now — flag when score < 1.0, and flag when score < 0.5 — and each score judge is
  decided on whichever of its two cutoffs has the higher balanced accuracy on B. Both are
  reported.
- **Decision items** = the 130 items the decision reads: B (70) + C (20) + D's 40 real rows.
  "Verdict on ≥ 95% of items" means 95% of these 130.
- **Retries.** Transport errors, timeouts and rate-limit refusals are retried up to 3 times with
  backoff; a free-tier daily cap is waited out and rescheduled, never recorded as a verdict.
- **No verdict** (after retries: unparseable output, NaN, refusal to judge, or a persistent
  timeout). Counted against the judge: a miss on a harmful or unrelated item, a false alarm on a
  clean item. The no-verdict rate is also reported.
- **Catch rate** = share of the 40 harmful planted items flagged.
- **False-alarm rate** = share of clean items flagged, where clean = the 30 benign planted items
  plus the real rows in D the human graded "No". Reported separately for each half and pooled;
  the decision uses the pooled value.
- **Coverage** = share of the 425 real rows the judge can read at all (L0: 69/425).

## Primary outcome (the metric that decides)

For each candidate judge: **catch rate and false-alarm rate**, read together, against fixed bars
and against the incumbent S on the same items.

## Decision rule

| Outcome | Threshold | Action |
|---|---|---|
| **Adopt** | All of: catch rate ≥ 0.80 and ≥ S's − 0.10; false-alarm rate ≤ 0.20 and ≤ S's + 0.05; flags ≥ 18 of 20 unrelated answers; verdict on ≥ 95% of items; coverage ≥ 90% | Recommend it as the golden-set faithfulness judge; wiring it into the benchmark is a separate issue judged by the release plan's gate bar |
| **Reject** | Any of: catch rate < 0.70; false-alarm rate > 0.35; flags < 16 of 20 unrelated answers; verdict on < 90% of items; coverage < 90% | Record the negative; keep S for faithfulness |
| **Inconclusive** | Anything in between | Double B (another 40 harmful + 30 benign, new seed) and re-read once; if still in between, keep S |

Notes fixed in advance:

- L0 cannot be adopted (coverage 16%). It is scored to learn whether it is accurate on the rows
  it reads, which decides whether a follow-up that trims documents to fit 8,000 tokens is worth
  proposing.
- S is held to the same absolute bars and the result reported. If S itself fails them (likely on
  false alarms, given the rule), that is a finding about the paid judge, not a reason to relax the
  bars.
- Sample size: 40 harmful items give a 95% interval of about ±12 points on a catch rate near 0.8;
  about 60 clean items give about ±10 points. The bars are coarse on purpose.
- More than one candidate may be adopted. Among adopted ones, prefer $0 per token over paid, then
  the higher balanced accuracy.

## Secondary analyses (planned)

- [ ] Per-type catch rate (the five harmful types) and per-length band (short / long), per judge.
- [ ] Paired catch rate on B (flagged the planted version, not the original).
- [ ] Human vs each judge on the 40 real rows: agreement, kappa, and the rows where they differ.
- [ ] Human catch rate on the 6 hidden planted items.
- [ ] Arm-level, **descriptive only**: new − old code on the 72 questions scored in all four A/B
      runs, and Qwen3.8 vs Qwen3.6 on the 52 shared questions, per judge, with question-bootstrap
      intervals. Caveat: the old-code runs lost more questions to missing parent passages (25 and
      23 vs 5 and 12), so these subsets are not representative of full runs.
- [ ] Continuity (mode a vs S on the 425 real rows): rank correlation, mean shift, and how often
      each crosses the 0.5 cutoff differently. This is early evidence for #737 step 5.
- [ ] Incumbent repeatability: re-score 20 real rows with S now and compare with the October
      scores.
- [ ] Cost: tokens and dollars per item for every judge; S's faithfulness-only cost per
      109-question run (October artifacts do not record judge usage, so this is the only
      measurement of faithfulness's share of the ~$28).
- [ ] Exploratory, only if cheap: L0's probability on "PASS" as a score instead of the verdict.

## Stopping rule

- **Target N:** B = 70 items, C = 20, D = 46 records, each submitted once.
- **Minimum submissions per record:** 1 (single grader; see deviations).
- **Hard deadline:** lock date + 14 days. If D is not finished by then, analyze with the graded
  rows, report the false-alarm rate from the benign half of B alone, and flag the shortfall.

## Pins and reproducibility

- OpenRouter: one host per model, `provider: {order: [<host>], allow_fallbacks: false}`, chosen
  at lock among full-precision hosts that serve ≥ 128k context (2026-10-27 listing: gpt-oss-120b
  bf16 on DeepInfra or Crusoe; Llama 3.3 70B fp16 on CoreWeave; Nemotron free has one host,
  Nvidia). `temperature 0`, `seed 20261027`, default reasoning effort. Record the host, model id,
  tokens, cost and latency returned with every response; OpenRouter does not pin weight
  revisions, so record the scoring dates.
- RAGAS: same `ragas` package version as the October runs (read it from the benchmark image used
  then); score inside that image if the host environment differs.
- Lynx v1.0 and v1.1: existing scorer at the archi commit recorded at lock; V100 settings from
  `RESULTS.md` (`VLLM_FLASH_V100_DISABLE_PAGED_PREFILL=1`; v1.1 at `--context-tokens 32000` with
  GPU memory share 0.85 via `v11/run_scorer_v100.py`, as in its existing real-row run).
- Keys: `JUDGE_OPENROUTER_API_KEY` in `~/.secrets/archi-secrets.env` (tested 2026-10-27; $10
  credit, $50/month key cap, 1,000 free-model requests/day). Never written to any output.

## Budget (estimate)

| Judge | Calls | Cost |
|---|---|---|
| S on B + C + 20 repeat rows (110 items) | ~220 | ≈ $5 (Bedrock, list price) |
| Ga, Ma, Gb, Mb on A + B + C (515 items each) | ~1,500 per mode-a judge, ~515 per mode-b judge | ≈ $1–3 each |
| Na, Nb (free) | ~1,500 total | $0; about 2 days at 1,000 requests/day |
| L0 on B-short + C-short | ~45 | $0, minutes on one V100 |
| L1 on B + C | ~90 | $0, about 20 minutes on one V100 |

Well under $20 in all. The operator's time (D) is the largest cost.

## What gets built (left to the builder unless stated)

1. **Planted-set builder:** picks base rows by the rule in B, writes the edits and the ledger,
   verifies each edit, emits the planted and control bundles.
2. **OpenRouter scorer** (script, not product code): for mode a, a short script reusing
   `judge_scorer.load_bundle` and `ragas_scoring.build_ragas_scorer` with
   `enabled_metrics=["faithfulness"]` and a `ChatOpenAI` judge pointed at
   `https://openrouter.ai/api/v1` (the offline judge in `scripts/benchmarking/judge/` only talks
   to a local vLLM socket, so it cannot be reused directly). For mode b, the frozen rule prompt
   with a JSON verdict. Checkpointed and resumable; respects the free tier's daily cap. The same
   script scores S on new items with the existing HUIT Bedrock judge factory.
3. **Argilla loader and export** for D.
4. **Analysis and report:** numbers recomputed in R and shown in the team's report-and-slide-deck
   format, with the decision table first.

Code and outputs live in `fasrc/archi-bench-out` under `checks/2026-10-free-faithfulness-judge/`,
not in archi's `src/`, so no product code changes. Large inputs stay under
`/scratch/a2rchi/bench-runs/lynx-vs-ragas/`.

## Deviations and risks (known at lock)

- **Single human grader.** No inter-rater agreement. Optional: one colleague grades a shared 15
  records; if done, report kappa.
- **Short questions are few.** L0's planted items come from at most 14 distinct questions (15
  with the fallback), and the cleanliness check may shrink that further.
- **Rebuilt rows only.** 120 of 545 questions (22%) are absent because archi answered from a
  whole document with no parent passage; judges are never tested on those.
- **Free-tier data handling.** OpenRouter does not state whether the free Nemotron host keeps or
  trains on prompts. Inputs are public FASRC documentation and golden-set questions.
- **Paraphrases are written by Claude**, the same model family as the incumbent S; all other
  edits are mechanical or from fixed lists. Verified mechanically; the 6 hidden in D also get a
  human read.
- **Same-family bias** no longer matters for the referee (the human is the referee), so Llama 3.3
  is a fair candidate even though Lynx is a fine-tuned Llama 3.
