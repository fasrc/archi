## ADDED Requirements

### Requirement: A single question failure does not abort the benchmark run

The benchmark harness SHALL isolate failures at the per-question boundary, spanning both
answering AND per-question scoring (source-match results and RAGAS input assembly). When
answering or scoring a question raises an exception, the harness MUST catch it, record a
clearly-marked failure entry for that question, and continue to the next question. One
question's failure MUST NOT propagate out of the run loop, discard already-computed results, or
prevent remaining questions from being answered and scored.

#### Scenario: An answer-generation error is contained

- **WHEN** answering a question raises an exception (e.g. a provider context-length 400)
- **THEN** the harness records a failure entry for that question capturing the error message
- **AND** continues answering the remaining questions
- **AND** the run completes and emits aggregate scores for the questions that succeeded

#### Scenario: A failed question is distinguishable from a low-scoring one

- **WHEN** the run finishes with at least one failed question
- **THEN** each failed question's result is marked as a failure with its captured error
- **AND** a failed question is not counted as a successful zero-score in the quality aggregate

#### Scenario: All questions succeeding is unchanged

- **WHEN** every question answers and scores without error
- **THEN** the run's results and aggregate scores are identical to the pre-change behavior
- **AND** no failure entries are recorded

### Requirement: Per-question resilience logic is unit-testable

The per-question failure-isolation logic SHALL live in a unit-importable helper so it is
covered by the test suite. The `src/bin` service entrypoint MUST remain a thin call site that
delegates to the helper, so patch coverage can be met without importing the service module.

#### Scenario: The helper is exercised directly by tests

- **WHEN** the test suite runs
- **THEN** the failure-isolation helper is imported and tested with both a succeeding answer
  callable and a raising answer callable
- **AND** the raising case produces a recorded failure without propagating the exception

### Requirement: A run whose questions all fail still completes without invalid aggregation

The harness SHALL NOT invoke RAGAS aggregation on an empty success set. When a configuration has
zero successful questions (every question failed), the run MUST complete and emit marked
`n/a` / skipped aggregates instead of constructing an empty RAGAS dataset or accessing metric
columns that do not exist.

#### Scenario: All questions in a config fail

- **WHEN** every question in a configuration fails (e.g. a provider outage or context overflow
  on every row)
- **THEN** the run completes without raising
- **AND** RAGAS metric computation is skipped for that configuration
- **AND** the aggregate for that configuration is recorded as `n/a` / skipped rather than a
  numeric score

### Requirement: Failed questions do not enter human-evaluation consumers

Failure entries SHALL be excluded from, or explicitly marked in, the downstream consumers that
iterate per-question results — A/B pairing (`pair_ab_results`) and the Argilla human-evaluation
export (`push_single_results_to_argilla`) — so a failed question is never presented as a
blank-answer gradeable record and never skews an A/B comparison.

#### Scenario: A failed question is not pushed to Argilla as a gradeable record

- **WHEN** the Argilla export runs over results containing a failed question
- **THEN** the failed question is skipped or flagged, not emitted as a normal blank-answer record

#### Scenario: A failed question is not paired into an A/B comparison as a real answer

- **WHEN** A/B pairing runs over results containing a failed question
- **THEN** the failed question is skipped or flagged rather than paired as a legitimate answer
