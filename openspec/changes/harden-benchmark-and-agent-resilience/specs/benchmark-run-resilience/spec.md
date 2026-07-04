## ADDED Requirements

### Requirement: A single question failure does not abort the benchmark run

The benchmark harness SHALL isolate failures at the per-question boundary. When answering or
scoring a question raises an exception, the harness MUST catch it, record a clearly-marked
failure entry for that question, and continue to the next question. One question's failure
MUST NOT propagate out of the run loop, discard already-computed results, or prevent remaining
questions from being answered and scored.

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
