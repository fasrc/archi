## ADDED Requirements

### Requirement: The feature-matrix QA wrapper SHALL record no ledger row for a QA run that scored nothing
`qa_arm.sh` SHALL exit with status 2 and append no ledger row when the run's `summary.json` is missing, is not readable JSON, or reports `attempt_lifecycle_counts.scored` as absent, not a positive integer, or `0`, on both the sweep path and the single-arm path.
The refusal message SHALL name the `summary.json` path and SHALL say that the run was NOT recorded. A run with `scored` of 1 or more SHALL be recorded even when some attempts failed. The check runs after the corpus-drift check and before the ledger append, and is one `lib.sh` helper, `fm_require_scored`, that both paths call.

#### Scenario: A run that scored nothing is refused
- **WHEN** a single-arm QA run writes `summary.json` with `attempt_lifecycle_counts.scored` equal to `0`
- **THEN** `qa_arm.sh` exits 2
- **AND** the message names the `summary.json` path and contains `NOT recorded`
- **AND** the ledger has the same number of rows as before the run

#### Scenario: A missing summary is refused
- **WHEN** a single-arm QA run leaves no `summary.json` in its output directory
- **THEN** `qa_arm.sh` exits 2 and appends no ledger row

#### Scenario: An unreadable summary is refused
- **WHEN** the run's `summary.json` holds text that is not valid JSON
- **THEN** `qa_arm.sh` exits 2 and appends no ledger row

#### Scenario: A partial run is recorded
- **WHEN** the run's `summary.json` reports `scored` 3 and `execution_failed` 2
- **THEN** `qa_arm.sh` exits 0 and appends exactly one ledger row

#### Scenario: The sweep path obeys the same rule
- **WHEN** a `qa_arm.sh --sweep` run's `summary.json` reports `scored` equal to `0`
- **THEN** `qa_arm.sh` exits 2 and appends no ledger row
- **AND** a later sweep run of the same arm whose summary reports `scored` 3 appends one row
