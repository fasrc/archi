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

#### Scenario: A failed refresh on a bank failure still names the bank error

- **WHEN** the bank fails to load AND the summary write for that failure also
  fails
- **THEN** both the bank error and the write error are printed to stderr
- **AND** the process exits non-zero
- **AND** any pre-existing summary is left intact (stale), which is why the exit
  code and not the file is the authoritative failure signal

### Requirement: A path the run writes SHALL NOT name a path it reads

The system SHALL refuse to run when any output path (`--summary-json`,
`--ledger`) names the same file as any other declared path — an input
(`--bank`, `--corpus-json`, `--sources`) or the other output. Sameness SHALL be
judged by file identity (device and inode) and, for a target that does not exist
yet, by resolved path — not by string equality. The refusal MUST happen before
the subcommand runs, so a rejected run reads, prints and writes nothing, and it
MUST name both colliding flags. The process SHALL exit non-zero.

#### Scenario: `--summary-json` names the bank

- **WHEN** `report --bank <p> --summary-json <p>` is run
- **THEN** the run is refused with an error naming `--summary-json` and `--bank`
- **AND** the file at `<p>` is byte-unchanged
- **AND** the process exits non-zero
- **AND** this holds whether the bank loads or fails to load

#### Scenario: The same file under a different spelling

- **WHEN** an output and an input name one file through a symlinked directory,
  a hard link, or a `..` segment
- **THEN** the run is refused exactly as for an identical string

#### Scenario: Two outputs that do not exist yet collide

- **WHEN** `--ledger` and `--summary-json` resolve to one path and neither file
  exists
- **THEN** the run is refused and neither file is created

#### Scenario: `--ledger` names the corpus dump

- **WHEN** `coverage --corpus-json <p> --ledger <p> --undecline <url>` is run
- **THEN** the run is refused with an error naming `--ledger` and
  `--corpus-json`
- **AND** the corpus dump at `<p>` is byte-unchanged

#### Scenario: Distinct paths in one directory are not a collision

- **WHEN** the bank, corpus, source list, ledger and summary are distinct files
  in the same directory
- **THEN** the run proceeds normally

### Requirement: A row's `anchor_type` SHALL be text or absent

The census buckets rows by `anchor_type`, so the system SHALL reject a bank in
which any row's `anchor_type` is present and not a string, BEFORE computing the
census. The rejection SHALL be an operational failure naming the field and the
offending row index, and SHALL take the startup-failure summary path
(`census: null`, `notify: true`, non-zero exit). Rejection MUST NOT depend on a
downstream crash: a value that happens not to crash anything must be refused on
its type alone.

#### Scenario: A number mixed with text

- **WHEN** one row's `anchor_type` is a number and another's is a string
- **THEN** the failure summary is written naming `anchor_type` and the row index
- **AND** the process exits non-zero, with no traceback

#### Scenario: Every `anchor_type` is a number

- **WHEN** a bank's only `anchor_type` values are numbers, so nothing downstream
  crashes and the census would report a stringified bucket
- **THEN** the bank is still rejected on the field's type

#### Scenario: A list or object

- **WHEN** a row's `anchor_type` is a list or an object
- **THEN** the bank is rejected the same way, rather than dying unhashable
  inside the census

#### Scenario: A row that is not an object at all is NOT a new refusal

- **WHEN** a bank row is a string or a number rather than an object, which
  `normalize_bank` passes through and the census has always counted under
  `unassigned`
- **THEN** the run still censuses that bank and completes normally — the
  `anchor_type` check MUST NOT tighten the bank contract beyond the field it
  validates

### Requirement: A replaced file SHALL keep the access its target had

The system SHALL copy an existing target's mode bits, owning user and group, and
extended attributes (POSIX ACLs included) onto the replacement before committing
it, for both files this tool replaces — the `--summary-json` summary and the
`--ledger` ledger. Both are committed with `os.replace` over a
`tempfile.mkstemp` file, which otherwise carries its own metadata across the
swap. Each step is best effort: a step the process lacks the privilege or
filesystem support to apply SHALL be skipped rather than fail the write. When no
target exists, the replacement SHALL keep `mkstemp`'s `0600`.

#### Scenario: A group-readable summary stays readable to that group

- **WHEN** an existing summary is `0640` owned by a group the monitor belongs to
- **AND** a report run replaces it
- **THEN** the replacement has the same mode and the same owning group

#### Scenario: An ACL naming a reader survives the replace

- **WHEN** an existing summary or ledger carries a POSIX ACL granting a named
  user or group read
- **AND** a run replaces that file
- **THEN** the ACL is present on the replacement, unchanged

#### Scenario: The ledger is covered by the same guarantee

- **WHEN** `coverage --decline`/`--undecline` replaces a ledger whose mode,
  group or ACL an operator had widened
- **THEN** that access survives the write
