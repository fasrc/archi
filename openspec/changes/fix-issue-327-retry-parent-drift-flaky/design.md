# Design — accepting a crashed attempt under a post-run live-validation stamp

## Context

A QA evaluation run over a dataset with live (time-sensitive) items checks each live item
against its oracle twice: once before the agent runs and once after. `_iter_live_decisions`
(`src/evaluation/qa/workflow.py:604-648`) returns, per prepared item, the first failing
check or `None`.

That decision drives two writers in `score`:

- `_iter_scoring_pairs` (`src/evaluation/qa/workflow.py:652-688`) yields an
  (item, answer) pair to the evaluator only when `validation is None`.
- `_iter_terminal_plan` (`src/evaluation/qa/workflow.py:696-739`) yields, per attempt slot,
  either `None` ("a scored result goes here") or a terminal result stamped
  `live_validation_failed`.

`score` (`src/evaluation/qa/workflow.py:822-838`) interleaves them into
`evaluation_results.jsonl`. The whole item is quarantined: a live failure means the oracle
answer we would judge against is not trustworthy, so no attempt of that item is judged.

The run phase writes one answer row per attempt slot, with two possible statuses
(`src/evaluation/qa/schema.py:21-24`): `answer_ready`, or `execution_failed` when the
agent raised (`src/evaluation/qa/phases.py:47-61`).

`RetryParentStore._load` re-derives the join from disk before any retry, to refuse a
tampered or truncated workspace. Its post-run branch
(`src/evaluation/qa/workspace.py:245-259`) is the only place that assumes a live-stamped
attempt must have produced an answer.

## Goals / Non-Goals

**Goals:**

- `open_retry_parent` accepts every workspace `score` and `retry` can write.
- The genuine-corruption rejections keep working: identity mismatch, provenance mismatch,
  duplicate answers, missing or extra results, a pre-run stamp carrying an answer, a
  post-run stamp carrying none.
- Summary counts and report rows for the drift-plus-flaky shape stay byte-identical.

**Non-Goals:**

- Changing which attempts are judged, or the quality denominator. Not touched.
- A new retry kind, or per-attempt live decisions. The item is the unit of quarantine.
- Any console or route change. The 400 disappears because `retry_plan` stops raising.

## Decisions

### The verifier is the wrong side; the producers stay as they are

Issue #327 leaves the choice open between two sides. The producers are right.

The alternative — have `_iter_terminal_plan` stamp `live_validation_failed` only over
`answer_ready` attempts and let an `execution_failed` attempt keep its own terminal status
— was rejected on three counts:

1. **It moves a benchmark number.** `build_summary`
   (`src/evaluation/qa/scoring.py:107-121`) increments `quality_k` for an
   `execution_failed` result and does not for a `live_validation_failed` one. Letting the
   crashed attempt keep its own status therefore adds it to the quality denominator of an
   item whose ground truth is known to be unverifiable for this run. Acceptance criterion 3
   of #327 requires scoring output to be unchanged, and the release plan puts benchmark
   integrity ahead of the features it measures.
2. **It weakens retry seeding.** `index_retry_attempt`
   (`src/evaluation/qa/workspace.py:338-343`) derives the retry kind from the result
   status, and `_retry_with_open_parent` (`src/evaluation/qa/workflow.py:952-966`) does a
   fresh pre-run live check for every item that has a `live_validation` retry, promoting it
   to an execution retry only once the item is stable
   (`promote_live_retry_to_execution`, `src/evaluation/qa/workspace.py:360-365`). An
   attempt kept as `execution` would be seeded to re-run the agent on an item whose
   baseline has not been re-established.
3. **It is two producers, not one.** The successor writer
   (`src/evaluation/qa/workflow.py:1140-1164`) stamps the same way, so the same edit would
   have to be made twice and kept in step.

Fixing the verifier is one branch of one function, changes no artifact the pipeline writes,
and states the contract the producers already keep.

### The contract is phrased over the answer-status set, not "any answer"

The post-run branch accepts exactly `{answer_ready, execution_failed}` rather than "any row
that exists". Today that is every member of `AnswerStatus`, so the two readings agree; the
explicit set is what keeps them agreeing if a third status is ever added. A new status would
then fail the verifier loudly instead of being waved through by a check that only asked
whether a row was present.

The pre-run branch is untouched. A pre-run failure means the agent never ran for that item,
so `_iter_terminal_plan` yields identities without consuming an answer and the run writes no
answer row. An answer under a pre-run stamp is still corruption.

### The fixture is built by the pipeline, not by hand

`open_retry_parent` verifies ten artifacts by sha256 (`src/evaluation/qa/workspace.py:114-132`)
before `_load` runs, so a hand-written fixture is ten files plus a hash table — and one that
proves only that the fix accepts what the test author imagined.

The regression tests instead drive `QAWorkflow().composite` with the existing fakes in
`tests/unit/evaluation/qa/test_live_workflow.py` (`SequenceInvoker`, `EvaluatorFactory`,
`AgentFactory`, `_dataset`, `_run`), adding an agent fake that raises for a chosen question.
The oracle value sequence decides the drift: equal pre-run and post-run values pass, a
changed post-run value is the post-run failure. What the test then feeds
`open_retry_parent` is a workspace `score` wrote.

`tests/unit/evaluation/qa/test_workspace.py` keeps the cheap direct guards that need no
scored run.

## Risks / Trade-offs

- **The verifier gets weaker by exactly one pairing.** A workspace whose answer row was
  edited from `answer_ready` to `execution_failed` under a post-run stamp is now accepted.
  That edit is indistinguishable from a real flaky attempt, because the two are the same
  bytes; no verifier can separate them. Everything that made the workspace tamper-evident —
  the artifact hashes and the provenance and identity checks — is untouched.
- **The fork diverges from the upstream pin.** `src/evaluation/qa/workspace.py` is
  `port-verbatim` at `bebfbe56`. The divergence is deliberate and one branch wide; #327
  carries the upstream report as a follow-up.

## Migration Plan

None. No artifact format, manifest field, or status value changes. Runs written before this
change are read by the new verifier under the same rules, minus the false rejection.

## Open Questions

None.
