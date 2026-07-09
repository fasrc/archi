## ADDED Requirements

### Requirement: Bank schema validation for the configured modes

The system SHALL provide `validate_bank(bank, benchmarking_configs)` that returns a list of
human-readable schema errors for a question bank under the modes in `benchmarking_configs`,
returning an empty list when the bank is valid. The bank MUST first be normalized with the
same `normalize_bank` the harness uses, and each item MUST be checked for the presence of
every field in `required_fields_for_modes(benchmarking_configs)`. The function MUST be pure
(no I/O) and MUST NOT raise on malformed items.

#### Scenario: Modern bank missing `sources` under SOURCES mode

- **WHEN** the bank items have `user_input` but no `sources`
- **AND** `benchmarking_configs.modes` includes `SOURCES`
- **THEN** `validate_bank` returns one error per item
- **AND** each error names the missing field `sources` and the keys the item does have

#### Scenario: Same bank passes without SOURCES mode

- **WHEN** the bank items have `user_input` but no `sources`
- **AND** `benchmarking_configs.modes` is `[RAGAS]` (no `SOURCES`)
- **THEN** `validate_bank` returns an empty list

#### Scenario: Legacy-dialect bank is normalized before checking

- **WHEN** a bank item uses the legacy dialect (`question`, `answer`) and carries `sources`
- **AND** `benchmarking_configs.modes` includes `SOURCES`
- **THEN** `validate_bank` returns an empty list for that item (the `question`→`user_input`
  normalization is applied before the presence check)

#### Scenario: Non-dict item is reported, not raised

- **WHEN** a bank contains an item that is not a dict
- **THEN** `validate_bank` returns an error for that item
- **AND** does not raise

### Requirement: Per-metric eligibility warnings

The system SHALL provide `bank_eligibility_warnings(bank, benchmarking_configs)` that
returns non-fatal warnings when a RAGAS metric will score on only a subset of the bank
because rows lack the metric's required column. Warnings MUST be emitted only when `RAGAS`
is among the modes, MUST use `metric_required_column` to decide which enabled metrics have a
data requirement, and MUST report the scored denominator.

#### Scenario: Context metric with some empty references

- **WHEN** `modes` includes `RAGAS` and `enabled_metrics` includes `context_recall`
- **AND** some rows have an empty `reference`
- **THEN** the returned warnings include one naming `context_recall`
- **AND** it reports how many of the total rows are eligible (e.g. `20/27`)

#### Scenario: No warning when all rows are eligible

- **WHEN** every row has a non-empty `reference`
- **THEN** no eligibility warning is returned for the context metrics

#### Scenario: No RAGAS mode, no eligibility warnings

- **WHEN** `modes` does not include `RAGAS`
- **THEN** `bank_eligibility_warnings` returns an empty list

### Requirement: File-level bank preflight

The system SHALL provide `preflight_bank_file(queries_path, benchmarking_configs)` that
loads the bank JSON at `queries_path`, normalizes it, and returns a tuple
`(errors, warnings)`. A file that is missing, unparseable, or not a JSON list MUST be
returned as a single hard error in `errors` (the function MUST NOT raise), so every caller
branches uniformly on `errors`.

#### Scenario: Valid file

- **WHEN** `queries_path` points to a JSON list of items satisfying the required fields
- **THEN** `errors` is empty
- **AND** `warnings` reflects per-metric eligibility (possibly empty)

#### Scenario: Missing or unparseable file

- **WHEN** `queries_path` does not exist or does not contain a JSON list
- **THEN** `errors` contains exactly one hard error describing the problem
- **AND** no exception is raised

### Requirement: `archi evaluate` fails fast on an invalid bank

`archi evaluate` MUST run the bank preflight after the benchmarking configuration is
resolved and BEFORE any deployment work (compose build, volume creation, deployment-file
preparation, or ingest). When the preflight returns errors, the command MUST abort with a
`ClickException` whose message lists the offending items, and MUST NOT create volumes or a
deployment directory. Preflight warnings MUST be logged and MUST NOT block the run.

#### Scenario: Invalid bank aborts before ingest

- **WHEN** `archi evaluate` is run with a config whose modes require a field the bank lacks
- **THEN** the command exits with an error naming the missing field
- **AND** no benchmark volumes and no deployment directory are created
- **AND** no data-manager ingest is started

#### Scenario: Valid bank proceeds

- **WHEN** `archi evaluate` is run with a bank that satisfies the required schema
- **THEN** the preflight passes and deployment proceeds as before
- **AND** any eligibility warnings are logged without blocking the run

### Requirement: Standalone bank validator

The system SHALL provide `scripts/benchmarking/validate_queries.py`, a command-line tool
that validates a bank against a benchmark config without deploying. It MUST accept a config
path (`-c`) and an optional queries-file override (`-q`), print any errors and warnings, and
exit non-zero when there are errors and zero otherwise. It MUST delegate validation to the
`benchmark_schema` library functions rather than reimplementing the schema rules.

#### Scenario: Exit non-zero on an invalid bank

- **WHEN** the tool is run against a config+bank where items miss a required field
- **THEN** it prints the missing-field errors
- **AND** exits with a non-zero status

#### Scenario: Exit zero on a valid bank

- **WHEN** the tool is run against a config+bank that satisfies the required schema
- **THEN** it exits with status zero
