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

### Requirement: A symlinked output path SHALL be followed, not replaced

The system SHALL resolve an output path's final component before writing, so a
`--summary-json` or `--ledger` naming a symlink updates the referent and leaves
the symlink intact — matching `open(path, "w")`, which is what a stable
deployment path (`current -> releases/42/report.json`) is configured for.
Resolution MUST happen BEFORE the temp file's directory is chosen, so the temp
file and the commit destination are always on one filesystem. A path that does
not exist yet, and a dangling symlink, SHALL resolve through their parent chain
and be created. The ledger's lock sidecar SHALL be named after the resolved
path, so two spellings of one ledger contend for one lock.

#### Scenario: The stable path survives and the real file is updated

- **WHEN** `--summary-json` (or `--ledger`) names a symlink to a file in another
  directory
- **THEN** after the run the path is still a symlink
- **AND** the referent holds the new content

#### Scenario: The symlink crosses a filesystem boundary

- **WHEN** the referent is on a different filesystem from the symlink
- **THEN** the write still commits — the temp file is created beside the
  referent, not beside the link, so `rename(2)` never has to cross a mount

#### Scenario: Two spellings of one ledger take one lock

- **WHEN** a ledger is reached through a symlink
- **THEN** the lock sidecar is named after the resolved ledger, not after the
  path as typed

#### Scenario: Following the link does not weaken the aliasing refusal

- **WHEN** `--summary-json` is a symlink whose referent is the `--bank`
- **THEN** the run is still refused and the bank is byte-unchanged — resolving
  makes this configuration destructive where clobbering the link was survivable,
  so the guard must hold

### Requirement: A ledger transaction SHALL resolve its path exactly once

The system SHALL resolve the `--ledger` path once when the lock is taken and use
that resolved path for the lock, the read and the write of one transaction.
Resolving independently at each step leaves the lock protecting a file the
transaction may no longer be operating on: a deployment that retargets the
advertised stable symlink after the lock is taken leaves the command holding the
old referent's sidecar while it reads and replaces the new one, where a
concurrent command is serialising on that file's own lock — the lost update the
lock exists to prevent, reintroduced by the resolution.

Scoped to the read-modify-write **inside** the lock. A read-only load taken
before the transaction opens — the coverage pass's — legitimately resolves at its
own open, and pinning it would claim a guarantee no lock is held for.

#### Scenario: The lock hands back the path it locked

- **WHEN** a ledger transaction takes the lock
- **THEN** the caller receives the resolved ledger path
- **AND** the read and the write of that transaction use it

#### Scenario: Retargeting the link mid-transaction does not move the write

- **WHEN** the symlink named by `--ledger` is retargeted after the lock is taken
- **THEN** the write still lands on the file whose lock is held
- **AND** the new referent is left untouched

### Requirement: A hard-linked output path SHALL be reported, not silently decoupled

The system SHALL write the file and SHALL print a warning naming the target when
an output path has more than one hard link. An atomic commit installs a new inode
under the target's name, so every other name for the old inode keeps the previous
contents — a monitor reading one of those names sits on a healthy snapshot
indefinitely while each run reports success.

This is a property of replace-based atomicity, not a defect to resolve away: a
consumer holding an open descriptor across the write goes stale identically, and
`realpath` cannot see hard links at all, because they are equal names for one
inode rather than a chain to follow.

Neither alternative is acceptable, and the requirement says so to keep a later
change from "fixing" it into one of them. Writing in place would restore the
shared inode and reopen the partial-write window the atomic write exists to
close. **Refusing** the write would leave the monitor reading the previous
healthy summary indefinitely — the failure the summary contract exists to close,
arrived at by a different route — because the summary IS the health signal.

#### Scenario: A hard-linked target is named on stderr

- **WHEN** `--summary-json` or `--ledger` names a file with another hard link
- **THEN** the run warns and names that path
- **AND** the write still commits, so the health signal is not withheld

#### Scenario: An ordinary target is silent

- **WHEN** the output path has a single link, or does not exist yet
- **THEN** no warning is printed
