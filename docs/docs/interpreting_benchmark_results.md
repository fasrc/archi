# Interpreting Benchmark Results

**Audience:** anyone who runs an Archi benchmark round or reads its output. No
background in retrieval systems, machine learning, or statistics is assumed.
Every term is defined the first time it appears.

**What this document is for.** [`benchmarking.md`](benchmarking.md) tells you how
to *run* a benchmark. This document tells you how to know whether the result
*means anything*. Those are different skills, and the second one is where eval
programmes usually go wrong.

**The problem in one sentence.** A number went up. Did the system get better, or
did the number just move?

Answering that is harder than it sounds, because Archi's benchmark scores move on
their own even when nothing about the system changes. This document explains why,
and defines the procedure that separates a real improvement from a wobble.

!!! warning "This document is a gate, not a suggestion"
    Section 4 defines a **mandatory checklist**. A benchmark result may not be
    reported as an improvement, or used to justify shipping a change, unless
    every item passes. This exists because we have already made exactly this
    mistake — see [The +0.017 that meant nothing](#a-worked-example-the-0017-that-meant-nothing).

---

## 1. What Archi does, and what a benchmark measures

Skip this section if you already know what retrieval-augmented generation is.

### 1.1 The system under test

Archi answers questions about FASRC documentation. It does not know those answers
in advance. Instead, when a user asks a question, Archi:

1. **Searches a corpus.** The *corpus* is our collection of documents — the FASRC
   documentation pages we have downloaded. Each page is split into small pieces
   called *chunks*, because a whole page is usually too big to feed a language
   model at once.

2. **Retrieves the chunks that look relevant.** Every chunk is converted into a
   list of numbers called an *embedding*, which captures its meaning; chunks with
   similar meanings have similar numbers. *Retrieval* means finding the chunks
   whose numbers are closest to the question's numbers. Archi also *reranks* —
   takes the top candidates and re-scores them more carefully with a slower,
   more accurate model.

3. **Writes an answer using those chunks.** The retrieved chunks are pasted into
   the prompt given to a language model (currently Qwen 3.6, running locally),
   which composes the final answer.

Steps 1–3 together are called **RAG**, or retrieval-augmented generation. The
important consequence: **Archi's answer quality depends on two separable things**
— whether retrieval found the right documents, and whether the model wrote a good
answer from them. A benchmark that cannot tell those apart cannot tell you what
to fix.

Throughout this document the thing being tested is called the **SUT**, for
*system under test*.

### 1.2 What a benchmark run does

A benchmark run takes a **question bank** — a JSON file of questions, each with a
known-good reference answer and a list of source URLs the answer should have come
from — and, for each question:

1. asks the SUT the question and records its answer and the chunks it retrieved;
2. hands the question, the answer, the retrieved chunks, and the reference answer
   to a second language model called the **judge**;
3. records the judge's scores.

Our question bank is `config/benchmarking/ragas-jeopardy-master.json` (73
questions). Our judge is Anthropic's Claude Sonnet 4.5, reached through Harvard
HUIT's Bedrock proxy.

### 1.3 Why a language model grades a language model

This looks circular, and it is worth being uneasy about. Two safeguards make it
defensible.

First, **the judge is a different model from the SUT**. If a model grades its own
output it reliably rates its own writing style higher. Using Claude to grade Qwen
breaks that symmetry. This is the *judge/SUT split*, described in
[`benchmarking.md`](benchmarking.md#judgesut-split).

Second, **the judge is pinned to an exact version**
(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`, not a rolling alias), so the
grader does not silently change between rounds.

The judge is still not perfect, and it is not deterministic — ask it the same
question twice and it may score differently. That non-determinism is the central
subject of Section 3.

For decisions that actually matter, LLM scores are supplemented by **human
grading** in Argilla. See [`benchmarking.md`](benchmarking.md#human-grading-via-argilla).

---

## 2. The metrics, in plain words

Archi reports two families of scores. All are between 0 and 1, higher is better.

### 2.1 The four RAGAS metrics

RAGAS is the open-source library (version 0.3.5) that computes these. Each metric
answers a different question, and — critically — **each one looks at a different
subset of the available information.**

| Metric | The question it answers | What it looks at |
|---|---|---|
| `answer_relevancy` | Does the answer actually address the question asked? | question + answer |
| `faithfulness` | Is every claim in the answer supported by the retrieved chunks? | answer + retrieved chunks |
| `context_precision` | Of the chunks retrieved, how many were actually useful? | chunks + reference answer |
| `context_recall` | Did retrieval find everything the reference answer needed? | chunks + reference answer |

Read that last column carefully, because it determines what each metric can
detect.

- **`faithfulness` is our hallucination detector.** It never looks at the
  reference answer. It asks only: did the model invent things that the retrieved
  documents do not support? A confident, well-written, completely fabricated
  answer scores near zero here and can still score highly on `answer_relevancy`.

- **`answer_relevancy` never looks at the retrieved chunks.** An answer can be
  perfectly relevant and entirely made up.

- **The two `context_*` metrics require a non-empty reference answer.** Rows
  without one are silently excluded from those two metrics only. (In code:
  `src/utils/benchmark_schema.py`, `_METRIC_REQUIRED_COLUMN`.) This is why the
  four metrics can each be averaged over a *different number of questions* in the
  same run — see [Denominator drift](#34-denominator-drift-the-quiet-one).

### 2.2 The two source metrics

These come from SOURCES mode, which checks whether the documents Archi retrieved
include the ones the question said it should have used.

- **`relative_source_accuracy`** — fraction of questions where *at least one*
  expected source was retrieved.
- **`source_accuracy`** — fraction of questions where *every* expected source was
  retrieved. Strictly harder, so always lower.

### 2.3 Which metric responds to which change

This table is the single most useful thing to internalise. If you change one part
of the system, only some metrics should move. If the wrong ones move, something
else changed too.

| If you change… | Expect movement in | Should barely move |
|---|---|---|
| chunking, reranking, retrieval weights | `context_precision`, `context_recall`, both source metrics | `answer_relevancy` |
| the system prompt, or the SUT model | `faithfulness`, `answer_relevancy` | the `context_*` metrics |

!!! danger "The coupling that breaks this table"
    Archi's agent decides *its own search queries* as it reasons. So changing the
    **prompt** changes what gets **retrieved**, which moves the `context_*`
    metrics for reasons that have nothing to do with the retriever.

    You therefore **cannot** attribute a `context_precision` change to a
    retrieval improvement unless the prompt was held fixed. Change one layer per
    experiment.

---

## 3. Why a number going up does not mean the system got better

Four things move these scores. Only one of them is "the system got better."

### 3.1 Randomness — the one everybody forgets

Both the SUT and the judge are *stochastic*: they sample from probability
distributions, so they do not return identical output for identical input.

- The SUT runs at `temperature: 0.3`. Temperature controls randomness; `0` means
  always pick the most likely next word, higher means sample more freely. At
  `0.3` the SUT writes a slightly different answer every time.
- The judge samples too, and it is a remote API we cannot seed.

The consequence: **run the exact same benchmark twice, change nothing, and the
scores will differ.** How much they differ is called the **noise floor** (also
the *minimum detectable effect*): the smallest change you are entitled to call
real. A measured improvement smaller than the noise floor is indistinguishable
from having run the same thing twice.

!!! failure "We have never measured our noise floor"
    Every `(config, corpus snapshot)` group in `bench_out/` contains exactly
    **one** run. Not once have we repeated a run to see how much the number moves
    on its own. Until we do, no delta we have ever reported is interpretable.

    Fixing this is [Procedure A](#procedure-a-measure-the-noise-floor). It is the
    first thing to do, and it needs no code changes.

### 3.2 The question bank changed

Scores are only comparable across rounds if the questions were the same. Ours have
not been: rounds have used a 9-question documentation bank, a 27-question
ServiceNow-ticket bank, and now the 73-question `ragas-jeopardy-master` bank.
Numbers from different banks are **different measurements of different things**
and must never be compared.

### 3.3 The corpus changed

If documents were re-ingested between two runs, retrieval had a different haystack
to search, and every retrieval metric moves for free.

The run metadata contains a field called `corpus_snapshot_id`, and it is easy to
misread. **It is a random UUID generated once per invocation** — not a fingerprint
of the corpus contents (`src/bin/service_benchmark.py:126`). Therefore:

- Two results **sharing** a `corpus_snapshot_id` definitely ran against one
  corpus, in one invocation. This is a reliable guarantee.
- Two results with **different** ids may or may not have used the same corpus.
  The id cannot tell you. It only tells you they were separate invocations.

You can pin the id across invocations with the `ARCHI_CORPUS_SNAPSHOT_ID`
environment variable, when you know for certain no re-ingest occurred.

### 3.4 Denominator drift — the quiet one

The harness protects a run from a single bad question: if a question crashes or
overflows the model's context window, it is marked `degraded` and excluded rather
than aborting the run. Separately, the judge sometimes fails to score one cell,
emitting `NaN` ("not a number"), which the aggregate skips.

Both behaviours are correct. Both are **silent**, and both change *which questions
were averaged*.

So two runs reporting `faithfulness` may be averaging over different question
sets. Comparing those two averages compares two different exams. Newer runs
report a per-metric denominator (`<metric>_scored`, e.g. `"71 of 73"`) — check it.

### A worked example: the +0.017 that meant nothing

On 2026-07-04 we recorded `faithfulness` at 0.594, against 0.577 on 2026-06-30,
and `answer_relevancy` at 0.862 against 0.751. It looked like progress. It was not
evidence of anything:

- **Noise floor unknown.** Single run each. We had no idea whether `+0.017` was
  larger than the wobble.
- **Different denominators.** The Jul 4 run marked question 6 `degraded` and
  averaged 8 questions; Jun 30 averaged 9.
- **Different code, and possibly a different corpus.** The Jul 4 change was
  resilience hardening, not quality work. Nothing in it should have improved
  answers at all.

Now re-run that same comparison **properly**, using [Procedure C](#procedure-c-compare-two-arms)
— paired per question, intersected on the 8 questions scored cleanly in both runs:

```
paired on 8 questions (9 baseline / 8 treatment)

answer_relevancy     n=  8  delta=+0.1422 +/- 0.1210 (SE)   not distinguishable
faithfulness         n=  8  delta=-0.0241 +/- 0.0845 (SE)   not distinguishable
context_precision    n=  5  delta=-0.0254 +/- 0.0362 (SE)   not distinguishable
context_recall       n=  8  delta=+0.1458 +/- 0.0734 (SE)   not distinguishable
```

Read the `faithfulness` row carefully. Comparing the two published averages says
faithfulness **improved by 0.017**. Comparing the same questions to themselves says
it **got worse by 0.024**. The sign flipped — because the reported averages were
computed over different question sets (9 rows versus 8), so they were never
measuring the same exam.

Note also `context_precision`, which paired on only **5** of the 8 questions: the
judge returned `NaN` for the rest, and the aggregate silently skipped them.

And not one of the four deltas is distinguishable from zero, even before we bring
the noise floor into it. The honest conclusion was "we learned nothing about
quality." This document exists so that conclusion is reached *before* the result is
circulated, not after.

### 3.5 Ceiling and floor effects

A metric only carries information in the middle of its range, roughly 0.4 to 0.8.

- Near the **ceiling** (scores ~0.95+), there is no headroom; a real improvement
  cannot show up. Our 9-question documentation bank had `answer_relevancy` at
  0.862 with several questions above 0.95.
- Near the **floor** (scores ~0.15), everything fails and improvements are lost in
  the rubble. Our 27-question ServiceNow bank scored roughly 0.13–0.40 across all
  four metrics.

Neither bank could have shown a moderate improvement. We do not yet know where the
73-question bank lands — [Procedure A](#procedure-a-measure-the-noise-floor) tells
us, and that is as valuable as the noise floor itself.

Our currently *sensitive* metrics — the ones with room to move in both directions
— are `faithfulness` (0.594) and `context_precision` (0.501).

---

## 4. The gate

A benchmark result **may not be reported as an improvement**, and **may not be
used to justify shipping a change**, unless all eight items below pass. For
exploratory runs where no decision is being made, the gate is advisory — but say
so explicitly when you circulate the numbers.

| # | Gate | Why |
|---|---|---|
| **G1** | A pre-registration is committed **before** the run | Stops post-hoc selection of whichever metric flatters the preferred config. Template: `docs/eval/preregs/_template.md` |
| **G2** | The noise floor has been measured for this bank + corpus | Without it, "better" is undefined. [Procedure A](#procedure-a-measure-the-noise-floor) |
| **G3** | Both arms ran against **one pinned corpus** | Otherwise retrieval metrics move for free. §3.3 |
| **G4** | Both arms used the **identical question bank**, and you recorded which | Different banks measure different things. §3.2 |
| **G5** | The comparison is **paired per question**, joined on question text | See [Procedure C](#procedure-c-compare-two-arms). Aggregate-vs-aggregate throws away most of your sensitivity |
| **G6** | Only questions scored cleanly **in both arms** are compared | Guards denominator drift. §3.4 |
| **G7** | The improvement exceeds **2× the noise floor** *and* 2× its own standard error | One threshold for "bigger than the wobble", one for "not a fluke of these 73 questions" |
| **G8** | The **anchors** hold, and no other metric regressed by more than one noise-floor unit | Catches the classic trap: raising `faithfulness` by teaching the model to hedge, while quietly tanking `answer_relevancy` |

### On G5: why paired comparison, and why it is free

Most of the variation in these scores is **between questions**, not between
systems. On our 9-question run, `answer_relevancy` ranged from 0.66 to 0.99.
Question 5 is simply harder than question 1, in both arms.

If you compare two *averages*, all of that question-to-question spread sits in
your error bars and drowns the effect you are looking for. If instead you compute a
**per-question difference** — question 1 scored 0.83 in the baseline and 0.88 in
the treatment, so its delta is +0.05 — the question's intrinsic difficulty appears
on both sides of the subtraction and **cancels out**.

Same data. Far more sensitivity. This is the single largest free win available,
and it costs one loop in a script.

Two traps:

- **Join on the question text** (`user_input`), never on the positional key
  `question_<n>`. The harness drops failed rows, so `question_7` in one run is not
  the same question as `question_7` in the other.
- **Intersect first, then average** (G6).

### On G8: the anchors

`examples/benchmarking/anchor_questions.json` holds five fixed questions that run
every round and are **never changed and never optimised against**. They are a
calibration weight: you weigh different things every day, but you also put the same
1 kg weight on the scale each morning, so you can tell when the *scale* has drifted.

| Anchor type | Count | How to read it |
|---|---|---|
| `easy_retrieve` | 2 | Should always score high. Any drop is an **alarm**: retrieval broke. |
| `reasoning` | 2 | Multi-document synthesis. The most sensitive **trend line** for prompt and model changes. |
| `should_refuse` | 1 | Asks about a non-FASRC system. Correct behaviour is to decline and refer. **Binary gate:** a confident wrong answer fails the round outright. |

Anchors are pass/fail tripwires and trend lines. They are **not** a measurement —
five questions cannot establish anything statistically. Do not average them into
the bank's score. (Today the harness does exactly that; see
[Gap 3](#gap-3-anchors-are-averaged-into-the-bank-and-should_refuse-corrupts-two-metrics).)

---

## 5. Procedures that work today

All commands assume the benchmark secrets file at `~/.archi/.env.benchmark`.

!!! note "How a benchmark run actually behaves"
    `archi evaluate --hostmode` **exits as soon as the deployment is up.** The
    real evaluation runs asynchronously inside the `benchmarking-<name>`
    container. Watch the container, not the CLI. With `--force` it re-ingests the
    corpus first, which takes roughly 50 minutes. Results land in `bench_out/`.

### Procedure A: measure the noise floor

Do this **once per (bank, corpus)** pair, before any comparison. It needs no code
changes.

The trick: `archi evaluate -cd <dir>` sweeps every config in a directory over a
**single, once-ingested corpus** in **one invocation**. Put three *identical
copies* of your config in that directory — differing only in filename — and you
get three independent evaluation passes on one corpus, sharing one
`corpus_snapshot_id`.

```bash
mkdir -p configs/noise
for i in 1 2 3; do
  cp config/benchmarking/ragas-jeopardy-master.yaml configs/noise/rep${i}.yaml
done

archi evaluate -n bench-noise -cd configs/noise/ -e ~/.archi/.env.benchmark --hostmode
```

The output JSON will contain three entries under `benchmarking_results`, one per
config file. For each metric, take the three `aggregate_<metric>` values and
compute their **standard deviation** (a measure of how much they scatter around
their average). That number is your noise floor, σ.

```python
import json, statistics

d = json.load(open("bench_out/<the-run>.json"))
arms = d["benchmarking_results"]

for metric in ["answer_relevancy", "faithfulness",
               "context_precision", "context_recall"]:
    vals = [a["total_results"][f"aggregate_{metric}"] for a in arms]
    print(f"{metric:20s} mean={statistics.fmean(vals):.4f} "
          f"noise floor sigma={statistics.stdev(vals):.4f}")
```

Three repeats give a rough σ; five give a better one. Record the result in the
pre-registration. **Any future improvement smaller than 2σ is not an improvement.**

This run also tells you whether the bank is stuck at a ceiling or a floor (§3.5).

### Procedure B: choose the right comparison shape

| Your change | Shape | Why |
|---|---|---|
| prompt, SUT model, RAGAS settings | **One** invocation, `-cd` with both configs | Retrieval config is baked into the once-ingested corpus, so both arms share it automatically. Same corpus is guaranteed. |
| chunking, retriever, embeddings | **Two** invocations, each re-ingesting | These change ingestion itself, so each arm must build its own corpus. |

For the two-invocation case you *cannot* pin the corpus, because building a
different corpus is the point. Instead pin the **inputs** to ingestion: identical
`config/lists/sources.list`, ingested from the same source state, as close together
in time as practical. Record both `corpus_snapshot_id` values and note in the
pre-reg that the corpora differ by design. Worked example:
[`benchmarking.md`](benchmarking.md#hierarchical-rerank-ab).

### Procedure C: compare two arms

Until `compare_runs.py` exists ([Gap 1](#gap-1-no-comparison-tool)), paste this
into a notebook. It implements G5 and G6.

```python
import json, math, statistics

METRICS = ["answer_relevancy", "faithfulness",
           "context_precision", "context_recall"]

def load(path, arm=0):
    """Clean rows of one arm, keyed by question text."""
    results = json.load(open(path))["benchmarking_results"][arm]
    return {
        row["question"]: row
        for row in results["single_question_results"].values()
        if row.get("status", "ok") == "ok"          # drop degraded/failed rows
    }

def real(x):                                        # a usable score, not NaN
    return isinstance(x, (int, float)) and not math.isnan(x)

baseline  = load("bench_out/<baseline>.json")
treatment = load("bench_out/<treatment>.json")

common = baseline.keys() & treatment.keys()         # G6: intersect, then average
print(f"paired on {len(common)} questions "
      f"({len(baseline)} baseline / {len(treatment)} treatment)")

for metric in METRICS:
    deltas = [treatment[q][metric] - baseline[q][metric]
              for q in common
              if real(baseline[q].get(metric)) and real(treatment[q].get(metric))]

    if len(deltas) < 2:
        print(f"{metric:20s} too few paired rows ({len(deltas)})")
        continue

    mean = statistics.fmean(deltas)
    se   = statistics.stdev(deltas) / math.sqrt(len(deltas))   # standard error
    print(f"{metric:20s} n={len(deltas):3d}  delta={mean:+.4f} +/- {se:.4f} (SE)"
          f"   {'SIGNIFICANT' if abs(mean) > 2 * se else 'not distinguishable'}")
```

The *standard error* (SE) estimates how much the mean delta would wobble if you
drew a different sample of 73 questions. `|delta| > 2 x SE` means "probably not a
fluke of these particular questions."

Then apply **G7 in full**: the delta must clear `2 x SE` **and** `2 x σ` (the noise
floor from Procedure A). The first says it is not an accident of *which questions*
you asked; the second says it is not an accident of *the day you ran it*. Both are
required, and they fail in different ways.

### Procedure D: reading the results file

```
bench_out/benchmarking-<name>-<timestamp>.json
├── metadata
│   ├── corpus_snapshot_id     # shared => ran together (see §3.3)
│   └── git_info.last_commit   # which code produced this
└── benchmarking_results[]     # one entry per config in a -cd sweep
    ├── configuration_file
    ├── total_results
    │   ├── aggregate_<metric>
    │   ├── <metric>_scored    # "71 of 73" — CHECK THIS (§3.4)
    │   ├── source_accuracy
    │   └── relative_source_accuracy
    └── single_question_results
        └── question_<n>
            ├── question       # join on THIS, not the key (G5)
            ├── status         # "ok" | "degraded" | ...
            ├── anchor_type    # anchors only: easy_retrieve|reasoning|should_refuse
            ├── difficulty     # bank rows only: easy|medium|hard
            └── answer_relevancy, faithfulness, context_precision, context_recall
```

Report the bank sliced by `difficulty`, not as one number. A single mean over 40
easy, 27 medium and 6 hard questions hides everything interesting. Treat the
`hard` slice as directional only — at n=6, one question swinging moves that mean
by 0.17.

---

## 6. Known gaps — NOT YET IMPLEMENTED

Everything above works today. Everything below does **not** exist yet. Do not
follow a step that silently does nothing.

### Gap 1: no comparison tool

Procedure C is a copy-paste snippet. It should be `scripts/benchmarking/compare_runs.py`,
refusing to run when the corpus snapshots or bank hashes disagree, and printing the
paired table, the difficulty slices, and the anchor pass/fail block.

### Gap 2: the question bank is not version-controlled

`config/benchmarking/ragas-jeopardy-master.json` is **gitignored**. The anchors
(`examples/benchmarking/anchor_questions.json`) are tracked, but the 73 questions
they ground are not. Nobody — including future us — can reconstruct what a past run
actually measured.

*Fix:* track the bank, and stamp a hash of its contents into `metadata` beside
`git_info`, so a comparison tool can refuse to compare across banks (G4).

### Gap 3: anchors are averaged into the bank, and `should_refuse` corrupts two metrics

The harness merges the 5 anchors into the question set and averages everything
together, with the SOURCES denominator set to the full question count. Two
consequences, both verified in code:

- **`should_refuse` books a free strict source hit.** It declares zero expected
  sources, and `source_hits` computes strict success as `all(matches)`, which is
  vacuously true for an empty list — so it returns `(0, 1)` regardless of what the
  SUT actually answered. `source_accuracy` is inflated, `relative_source_accuracy`
  is diluted (`src/utils/benchmark_resilience.py:64`).
- **A correct refusal scores near zero on the `context_*` metrics**, because its
  reference answer ("I don't have documentation covering that") is by construction
  unsupported by any retrieved FASRC chunk. We penalise exactly the behaviour we
  are testing for.

*Fix:* report anchors as a separate track; exclude them from the bank aggregates;
score `should_refuse` as a binary assertion (did it decline, did it avoid inventing
names) rather than feeding it to RAGAS.

*Status:* this gap is **live as of the change that shipped this page**. Anchors were
enabled by default but never actually ran — the anchor file never reached the
benchmarking container, so the harness logged "running without anchors" and graded
only the bank. That delivery bug is now fixed, which means anchors score for the
first time, which in turn means the flaw above now affects real numbers.

> **Do not compare a run from after that fix against a baseline from before it.**
> The graded set goes from 73 questions to 78, which violates G4 and G6. Every
> benchmark result recorded before this page existed — including the July 4
> floor baseline — is a 73-question measurement. Re-baseline first.

### Gap 4: the benchmark SUT samples at temperature 0.3

`temperature: 0.3` adds sampling noise to every question, in both arms, for no
measurement benefit. Benchmarking at `temperature: 0` would shrink the noise floor
substantially.

The counter-argument is real: you would then be benchmarking a configuration you do
not ship. The resolution is to benchmark at `0` for **sensitivity** when comparing
two arms, and separately confirm the winner at the production temperature. Decide
this explicitly in the pre-reg.

*Not a gap:* RAGAS already defaults to `seed=42` and `max_workers=16`, and our
`timeout: 180` merely restates RAGAS's own default. `batch_size: false` becomes
"no batching", which is correct. None of these need changing, and none of them
affect scores.

### Gap 5: no held-out question set

We have a development bank and anchors. We have no **held-out** set — questions
reserved to confirm that a gain generalises rather than being tuned into those
specific 73 items.

Without it, months of iterating against a fixed bank produces a system that is
measurably better at those 73 questions and nowhere else. This is called
*overfitting*, and it is invisible from inside the benchmark.

*Fix:* a three-way split — development bank (iterate freely), held-out bank (run
rarely, never tune against), anchors (never touched).

### Gap 6: `difficulty` is not visible in human grading

The bank's `difficulty` field is not pushed to Argilla; only `anchor_type` is
declared as a metadata property (`src/utils/benchmark_argilla.py:433`). Slicing
human grades by difficulty needs a new `TermsMetadataProperty`.

### Gap 7: bank coverage is uneven

Of the 73 questions, 8 draw on `modules-intro` and 6 on `slurm-intro`. Over-sampling
a few pages means a retrieval change touching those pages swings the whole score
disproportionately. Spread the sources when the bank is next revised.

---

## 7. Glossary

**Anchor** — a fixed question, never edited and never optimised against, run every
round to detect drift. See §4/G8.

**Bank** — the JSON file of benchmark questions with reference answers and
expected sources.

**Ceiling / floor effect** — when scores are so high (or so low) that a real
improvement cannot show up in the number. §3.5.

**Chunk** — a small piece of a document; retrieval operates on chunks, not whole
pages.

**Corpus** — the full collection of ingested documents Archi searches.

**Denominator drift** — when two runs average their scores over different numbers
of questions, making the averages incomparable. §3.4.

**Embedding** — a list of numbers representing a piece of text's meaning, so that
similar texts have similar numbers.

**Ingestion** — downloading, chunking, and embedding the corpus. Takes ~50 minutes.

**Judge** — the language model that scores the SUT's answers. Ours is Claude
Sonnet 4.5, pinned.

**Noise floor (σ)** — how much a score moves when you re-run the identical
benchmark and change nothing. The smallest difference you may call real is 2σ.
Procedure A.

**Paired comparison** — comparing per-question differences rather than two overall
averages, which cancels out how hard each question intrinsically is. §4/G5.

**Pre-registration** — writing down the hypothesis, the deciding metric, and the
decision rule *before* running the eval, and committing it. `docs/eval/preregs/_template.md`.

**RAG** — retrieval-augmented generation: search a corpus, then write an answer
from what you found. §1.1.

**Reranking** — re-scoring the top retrieval candidates with a slower, more
accurate model.

**Standard deviation** — a measure of how spread out a set of numbers is around
their average.

**Standard error (SE)** — how much an average would wobble if you had sampled a
different set of questions. Shrinks as the bank grows.

**SUT** — system under test; the Archi configuration being benchmarked.

**Temperature** — how randomly a language model picks its next word. `0` is
deterministic; higher is more varied.

---

## See also

- [`benchmarking.md`](benchmarking.md) — how to run a benchmark, the CLI, Argilla,
  the prompt sweep, and the retrieval A/B.
- `docs/eval/rubric.md` — the human annotation rubric and calibration protocol.
- `docs/eval/preregs/_template.md` — the pre-registration template (G1).
