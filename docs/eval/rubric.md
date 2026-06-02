# Annotation rubric for archi human grading

This is what evaluators see in Argilla when grading a benchmark record. It's the same for every round so inter-round comparisons stay meaningful.

## What you'll see per record

For an **A/B comparison** record:

| Field | Type | Visible to grader |
|---|---|---|
| Question | text | yes |
| Reference answer | text | yes |
| Response A | markdown | yes — **NOT labelled with config name** |
| Response B | markdown | yes — **NOT labelled with config name** |
| Agent trace A | collapsible | yes (collapsed by default) |
| Agent trace B | collapsible | yes (collapsed by default) |
| RAGAS scores (per response) | float metadata | **hidden from grader during annotation** |

The config name is hidden so you grade the answer on its own merit, not on the model/prompt label.

For a **single-config** record (no A/B), there's just one Response column; otherwise identical.

## What you annotate (four widgets)

### 1. Winner — `winner: [A, B, Tie]` (required)

Which response is better? Three options only.

- **A** or **B** — one is meaningfully better than the other.
- **Tie** — they're both fine in different ways, OR they're both bad in different ways. Use Tie when you genuinely can't pick; **don't use Tie as a "default" when you haven't read carefully**.

This is binary-ish (A vs B with a Tie escape hatch) **not Likert**. Three reasons:
- People are bad at calibrating "5 vs 6" — the boundaries are fuzzy. They're much more reliable at "which of these two".
- A/B preference is what we ultimately use to decide adoption (will FASRC users find this better?). Likert ratings don't directly map.
- Pairwise comparisons are easier to aggregate into win-rates and easier to inter-rate-reliability check.

### 2. Quality — `quality: [1, 2, 3, 4, 5]` (required)

Quality of the **winning** response (or the one you'd pick if forced; for Tie, the average).

- **1** — Wrong, dangerous, or hallucinated. Would actively mislead the user.
- **2** — Mostly wrong, or right but missing critical caveats (e.g. would cause job failure).
- **3** — Adequate. Gets the user 70% of the way; they'd need to ask a follow-up.
- **4** — Good. Complete, correct, well-cited.
- **5** — Excellent. Would close a support ticket on first try.

This IS Likert and that's fine here — we don't use absolute quality for the adoption decision, we use it for context. Quality 5 vs 4 is fuzzy; you don't need to be precise.

### 3. Failure-mode tags — `notes` field (optional, free-text)

If the response is wrong or weak, **tag the failure mode** in `notes` using one of these tokens (compose freely; multiple per response is fine):

| Tag | Meaning |
|---|---|
| `#hallucination` | Invented facts not in any retrieved doc |
| `#wrong-path` | Cited the wrong filesystem path / module / partition name |
| `#stale` | Cited outdated info that's been superseded |
| `#missed-search` | Didn't retrieve docs that exist and would have answered cleanly |
| `#over-search` | Did multiple redundant searches when one would have done |
| `#refused-incorrectly` | Said "I don't know" when the answer was in the docs |
| `#hallucinated-confidence` | Wrong but stated with full confidence (worst kind) |
| `#format-issue` | Right answer but unparseable / wall-of-text / bad markdown |

Aggregating tag distributions across rounds tells us **what's getting worse vs what's getting better**, which raw scores don't.

### 4. Notes — free text (optional)

Anything else worth a sentence. Especially useful for **edge cases the rubric doesn't cover** ("this would be fine for a power user but wrong for a beginner") — these become inputs to the next rubric revision.

## Calibration round (do this first)

Before independent grading on real data, all evaluators do a **calibration round** together:

1. Pick 10 records from a past benchmark (NOT the current eval).
2. Everyone grades the 10 records independently in Argilla.
3. Come together (Zoom or in-person) and compare results.
4. For each record where graders diverged: discuss why. Often it's a definitional gap ("I thought 'Tie' meant equally good, you thought equally bad").
5. Adjust shared expectations and **document the resolutions** as additions to this rubric.
6. Then start the real eval.

Skip this and your inter-rater reliability will be terrible and you won't know whether the eval round is signal or noise.

## What NOT to do

- **Don't peek at the RAGAS scores** while grading. They're in the metadata for the analysis notebook, not for graders.
- **Don't compare with other graders mid-round.** That's calibration; we already did it. Independent grading is the whole point.
- **Don't skip records you find boring.** The 6th of 8 routine partition questions matters — that's where consistency lives.
- **Don't grade against your own preferred answer.** Grade against the question. If the user asked something narrow, the better response is the one that answered narrowly — not the one that volunteered extra info.

## Coverage target per record

We set `min_submitted: 2` (or 3 for high-stakes rounds) in `config/benchmarking/ragas.yaml`. A record is only counted in the final analysis after that many graders submitted.
