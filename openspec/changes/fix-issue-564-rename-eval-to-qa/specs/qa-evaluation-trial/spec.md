## ADDED Requirements

### Requirement: The QA evaluator CLI is named archi qa with a deprecated archi eval alias

The CLI SHALL register the QA evaluator group as the top-level command `archi qa`, with the composite mode and the `prepare`, `run`, and `score` subcommands. The CLI SHALL also keep `archi eval` as a hidden alias that runs the same `qa` group; each invocation through the alias MUST print one deprecation line to stderr and MUST leave stdout and the exit code identical to the `archi qa` path. `archi --help` MUST list `qa` and MUST NOT list `eval`. The behaviour of `archi evaluate` MUST NOT change.

#### Scenario: The new name runs every phase

- **WHEN** `archi qa run --help` and `archi qa score --help` run
- **THEN** each exits 0 and prints the usage of that subcommand
- **AND** nothing is written to stderr

#### Scenario: The old name still runs and warns

- **WHEN** `archi eval qa run --help` runs, or `archi eval qa` runs with the composite options
- **THEN** the exit code and stdout equal those of the same arguments under `archi qa`
- **AND** stderr carries the deprecation line exactly once

#### Scenario: The alias is hidden from the top-level help

- **WHEN** `archi --help` runs
- **THEN** the command list contains `qa` and `evaluate`
- **AND** the command list does not contain `eval`

#### Scenario: The feature-matrix wrappers call the new name

- **WHEN** `qa_arm.sh` or `qa_prepare.sh` invokes the CLI
- **THEN** the recorded call starts with `qa` and not with `eval qa`
