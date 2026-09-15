## ADDED Requirements

### Requirement: One log file per report run

The cron wrapper that drives the read-only `report` pass SHALL write each run's output to its
own file in `GOLDENSET_LOG_DIR`, named `goldenset-report-<YYYYMMDD>T<HHMMSS>Z.log` from the
run's UTC start time. It SHALL NOT append a run to a previous run's file, and SHALL NOT rotate
a shared log to a `.1` suffix. Each file SHALL keep the existing
`===== goldenset report <timestamp> =====` header and `===== exit <status> =====` footer, so a
file read on its own states what it is and how the run ended.

A single run's logged output SHALL remain capped at `GOLDENSET_LOG_MAX_BYTES` with the existing
truncation marker, so no one file can grow without bound. The **number** of files is
deliberately unbounded: the wrapper SHALL NOT delete old logs, because a nightly run is ~60 KB
and an automatic pruner trades immaterial disk for a way to silently lose history. Operators
prune by hand.

When a run has findings, the notification digest SHALL name the file that run wrote, not a
shared log path, so the digest line is sufficient to open the right report.

#### Scenario: Each run writes its own file

- **WHEN** the wrapper runs
- **THEN** it creates exactly one new `goldenset-report-<stamp>Z.log` in the log directory
- **AND** that file contains the run's header, the report output, and the exit footer

#### Scenario: A previous run's log is left intact

- **GIVEN** a log directory already holding a file from an earlier run
- **WHEN** the wrapper runs again
- **THEN** the earlier file's contents are unchanged
- **AND** the new run's output appears only in the new file

#### Scenario: No shared log is rotated

- **GIVEN** `GOLDENSET_LOG_MAX_BYTES` is set below the size of an existing log file
- **WHEN** the wrapper runs
- **THEN** no `.log.1` file is created

#### Scenario: A single run's output is still capped

- **GIVEN** a report run whose output exceeds `GOLDENSET_LOG_MAX_BYTES`
- **WHEN** the wrapper writes that run's file
- **THEN** the logged copy is truncated at the cap and carries the truncation marker
- **AND** on a failing run the untruncated output still reaches stderr

#### Scenario: The digest names the run's own file

- **WHEN** a run has findings and the wrapper emits its one-line digest
- **THEN** the accompanying `full report:` path is the file this run wrote

### Requirement: Stable pointer to the most recent report

The wrapper SHALL maintain a `goldenset-report-latest.log` symlink in `GOLDENSET_LOG_DIR`
pointing at the file the current run wrote, refreshed on every run that produces a log. The
link target SHALL be relative to the log directory, so the directory can be moved or copied
without the link dangling. Reading the most recent report MUST therefore require no glob, no
directory listing, and no timestamp arithmetic.

A run that refuses before invoking anything (a misconfigured wrapper) writes no log and SHALL
leave any existing symlink untouched, so `latest` never points at a run that did not happen.

#### Scenario: The symlink follows the newest run

- **WHEN** the wrapper completes a run
- **THEN** `goldenset-report-latest.log` resolves to that run's file
- **AND** reading through the symlink yields that run's output

#### Scenario: A second run repoints the symlink

- **GIVEN** a log directory where `latest` resolves to an earlier run's file
- **WHEN** the wrapper runs again
- **THEN** `latest` resolves to the newer run's file
- **AND** the earlier file still exists on disk

#### Scenario: A misconfigured run leaves the pointer alone

- **GIVEN** `latest` resolves to a previous run's file
- **WHEN** the wrapper exits 2 because a required setting is missing
- **THEN** no new log file is created
- **AND** `latest` still resolves to the previous run's file
