## ADDED Requirements

### Requirement: The `--summary-json` file is written on every terminating path

When `report` is invoked with `--summary-json <path>`, the system SHALL write a
summary file to `<path>` on every path that terminates the `report` run,
including the path where the question bank fails to load before any pass runs.
The write MUST NOT be reachable only after a successful bank load.

#### Scenario: Bank loads and passes run

- **WHEN** `report --summary-json <path>` is run against a loadable bank
- **THEN** a summary file is written to `<path>` containing the census and the
  per-pass results
- **AND** the process exit code reflects whether any pass failed

#### Scenario: Bank fails to load (missing / unreadable / malformed)

- **WHEN** `report --summary-json <path>` is run and `load_bank` raises
  `OperationalError` before any report pass runs
- **THEN** a summary file is still written to `<path>`
- **AND** the process exits non-zero

### Requirement: A startup-failure summary marks the run as broken, never stale-success

On a bank-load (startup) failure, the summary written to `--summary-json` SHALL
identify the run as failed rather than leave a prior run's success in place. The
summary MUST record the bank load error under `failed_passes`, set `census` to
`null`, and set `notify` to `true`, so a monitor reading the file alone sees a
failure signal.

#### Scenario: A pre-existing healthy summary is overwritten with the failure state

- **WHEN** a summary file from a previous healthy run already exists at `<path>`
- **AND** `report --summary-json <path>` is then run against a bank that fails to load
- **THEN** the file at `<path>` is overwritten with a summary whose
  `failed_passes` names the bank error, whose `census` is `null`, and whose
  `notify` is `true`
- **AND** the process exits non-zero
- **AND** the previous run's success values are no longer present in the file

### Requirement: The summary write is atomic

The system SHALL write the `--summary-json` file atomically — to a same-directory
temp file that is then `os.replace`d over the target — so a crash or full disk
mid-write cannot leave a truncated or partially written summary. This applies to
both the normal and the startup-failure write paths.

#### Scenario: A write interrupted before commit leaves the prior file intact

- **WHEN** the summary write fails before the `os.replace` commit point
- **THEN** no truncated summary is left at `<path>` (any pre-existing file is
  either intact or absent, never half-written)
- **AND** the temp file is not left behind
