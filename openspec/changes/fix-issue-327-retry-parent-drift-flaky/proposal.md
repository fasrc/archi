# Accept a crashed attempt under a post-run live-validation stamp

## Why

`EvaluationWorkspace.open_retry_parent` rejects a workspace that the scoring pipeline
itself produced. The two sides disagree about one attempt shape:

- **Producer.** `_iter_terminal_plan` (`src/evaluation/qa/workflow.py:704-740`) stamps
  `"status": "live_validation_failed"` on *every* ordinal `1..attempts` of an item whose
  live check failed. It never looks at the answer row, because the item is quarantined as
  a whole: `_iter_scoring_pairs` (`src/evaluation/qa/workflow.py:657-688`) yields a pair
  only when `validation is None`, so no attempt of that item ever reaches the evaluator.
- **Verifier.** `RetryParentStore._load` (`src/evaluation/qa/workspace.py:245-259`)
  requires every *post-run* `live_validation_failed` result to pair with an
  `answer_ready` answer, and raises
  `ValueError("parent run answer and live-validation phase disagree")` otherwise.

An agent exception is recorded as an answer with `"status": "execution_failed"`
(`src/evaluation/qa/phases.py:47-61`). So an item that both drifted after the run and had
a flaky attempt scores cleanly (`status: scored`, `summary.json` and `report.md` written)
and is then unretryable forever: the console retry button
(`src/evaluation/qa/console.py:545-551` -> `src/interfaces/chat_app/evaluation_routes.py:363-372`)
returns 400 with an integrity error, and the CLI `retry` fails the same way. Drift and a
flaky attempt are the two conditions retry exists for.

The same disagreement sits on the retry path: the successor writer
(`src/evaluation/qa/workflow.py:1140-1164`) re-stamps `live_validation_failed` over every
attempt of an item whose fresh check still fails, so a retry of a retry hits it too.

`src/evaluation/qa/workspace.py` is `port-verbatim` upstream code (pin `bebfbe56`,
`openspec/changes/archive/2026-08-21-port-live-eval-trial/disposition.md:130`), so the
defect is upstream's as well as ours. This change fixes the fork; issue #327 records that
the defect is reported on `archi-physics/archi` PR #608 after the fork fix merges.

This blocks #320 (safe console activation): retry is a console feature.

## What Changes

- `RetryParentStore._load` accepts a post-run `live_validation_failed` result whose answer
  row is `answer_ready` **or** `execution_failed`, and keeps rejecting every other pairing:
  a post-run stamp with no answer row, a pre-run stamp with one, and any answer status
  outside that two-member set.
- No producer changes. `_iter_terminal_plan` and the successor writer keep stamping the
  whole item, which is what keeps a drifted item out of the quality denominator
  (`src/evaluation/qa/scoring.py:107-121`: `execution_failed` increments `quality_k`,
  `live_validation_failed` does not) and what keeps retry seeding gated on a fresh
  pre-run check (`src/evaluation/qa/workflow.py:952-966`).
- Tests build the shape through the real pipeline, so the fixture is a workspace the
  scoring pipeline actually writes rather than one hand-assembled to match the fix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None.

### Added Requirements

- `qa-evaluation-trial`: the capability spec exists at
  `openspec/specs/qa-evaluation-trial/spec.md` but states nothing about retry or about
  what `open_retry_parent` treats as a corrupt workspace. This change **adds** that
  requirement rather than modifying one.

## Impact

- `src/evaluation/qa/workspace.py` — the post-run branch of `_load` only.
- `tests/unit/evaluation/qa/test_live_workflow.py` — every new test, including the
  corruption guards. Issue #327 put the RED test in
  `tests/unit/evaluation/qa/test_workspace.py`; that file has no scored-run fixture, and
  `open_retry_parent` verifies ten artifacts by sha256 before `_load` runs, so a fixture
  built there would be hand-written bytes rather than what the pipeline writes.
- Unblocks the console retry button for the commonest failure shape; unblocks #320.
- Scoring output is unchanged by construction: no producer and no summary code is touched.
