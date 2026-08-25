## ADDED Requirements

### Requirement: Retry admits every scored workspace the pipeline writes

`EvaluationWorkspace.open_retry_parent` SHALL accept a post-run `live_validation_failed` result whose attempt answer is `answer_ready` or `execution_failed`, and SHALL reject any other pairing of an answer status with a live-validation phase.

A live check that fails after the run quarantines the whole item: no attempt of it is
judged, and every attempt slot is written as `live_validation_failed`
(`src/evaluation/qa/workflow.py:704-740`). The run phase records an agent exception as an
answer with status `execution_failed` (`src/evaluation/qa/phases.py:47-61`), so an item
that both drifted and had a flaky attempt produces exactly this pairing. The verifier that
guards retry rejected it, which made a clean `scored` run unretryable forever through both
the console button and the CLI.

Post-run and pre-run are not symmetric. A pre-run failure stops the agent from ever running
on that item, so no answer row is written for it and an answer under a pre-run stamp remains
corruption. A post-run stamp, by contrast, always sits over a slot the run phase filled.

The accepted set is named, not implied. `answer_ready` and `execution_failed` are today the
only members of `AnswerStatus` (`src/evaluation/qa/schema.py:21-24`), so "these two" and
"any answer that exists" pick out the same workspaces now. They stop agreeing the moment a
third status is added, and the named set is what makes that new status fail the verifier
rather than pass unexamined.

#### Scenario: A drifted item with a crashed attempt opens for retry

- **WHEN** a run scores a live item whose post-run oracle value changed and whose attempt raised, so the workspace holds an `execution_failed` answer under a post-run `live_validation_failed` result
- **THEN** the run's status is `scored` and `open_retry_parent` returns a store
- **AND** the retry plan counts that attempt as retryable

#### Scenario: A retry of a retry opens

- **WHEN** the successor of that run still finds the item drifted, so it re-stamps `live_validation_failed` over the same slots
- **THEN** `open_retry_parent` accepts the successor workspace too

The successor writer (`src/evaluation/qa/workflow.py:1140-1164`) stamps the same way the
first run does. A fix that admitted only the first generation would move the dead end one
retry further out.

#### Scenario: A pre-run stamp carrying an answer is still corruption

- **WHEN** a workspace pairs a pre-run `live_validation_failed` result with any answer row
- **THEN** `open_retry_parent` raises `ValueError`

#### Scenario: A post-run stamp carrying no answer is still corruption

- **WHEN** a workspace holds a post-run `live_validation_failed` result whose attempt has no answer row
- **THEN** `open_retry_parent` raises `ValueError`

#### Scenario: Identity and provenance rejections are unchanged

- **WHEN** a workspace carries an attempt whose identity does not match the prepared membership, or whose recorded agent-config or agent-spec hash differs from the manifest
- **THEN** `open_retry_parent` raises `ValueError`

### Requirement: A drifted item stays out of the quality denominator

The scored output of a run SHALL count a `live_validation_failed` attempt outside the quality denominator, whatever answer the run phase recorded for that attempt.

`build_summary` (`src/evaluation/qa/scoring.py:107-121`) increments `quality_k` for an
`execution_failed` result and not for a `live_validation_failed` one. An item whose oracle
answer moved cannot be judged in either direction, so counting one of its attempts as a
quality failure would charge the agent for the item's drift.

This requirement is what fixes the verifier rather than the writer. Letting a crashed
attempt keep its own terminal status under a live stamp would silently change a benchmark
number for the mixed shape, and the number would move only for runs that happened to have
both conditions — the kind of drift a benchmark cannot afford.

#### Scenario: Summary counts are unaffected by the crashed attempt

- **WHEN** a run scores the drifted item whose attempt crashed
- **AND** an otherwise identical run scores the same drifted item whose attempt succeeded
- **THEN** both runs report the same `quality_k` for that item and the same
  `live_validation_failed` attempt count
- **AND** neither run reports an `execution_failed` attempt for it

#### Scenario: Retry re-checks the baseline before it re-runs the agent

- **WHEN** the retry of a run seeds an item that carries a live-validation retry
- **THEN** it observes a fresh pre-run live check for that item first
- **AND** it re-runs the agent for that item's attempts only once the fresh check matches the prepared baseline

The retry kind comes from the result status
(`src/evaluation/qa/workspace.py:338-343`). Keeping the crashed attempt under
`live_validation` is what holds it behind the fresh check
(`src/evaluation/qa/workflow.py:952-966`); an attempt seeded as a plain execution retry
would re-run the agent on an item whose baseline is still unverified.
