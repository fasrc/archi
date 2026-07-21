## ADDED Requirements

### Requirement: Machine-queryable confirmation state

Each bank row SHALL carry a `status` of `draft` or `locked`, and a row MUST NOT be treated as
authoritative ground truth unless it is `locked`. A `locked` row SHALL also record a
`source_hash` — the content hash of its `sources` grounding page captured at lock time. These
fields SHALL be loader-compatible: the benchmark harness requires only `user_input` (plus
`sources` for SOURCES mode) and MUST continue to load banks whether or not the fields are
present, so benchmark eligibility and scoring are unchanged.

#### Scenario: Absent status is treated as draft

- **WHEN** a bank row has no `status` field
- **THEN** the maintenance tooling treats it as `draft` (not authoritative)
- **AND** the benchmark harness loads and scores it exactly as before this change

#### Scenario: Locking a row records its grounding hash

- **WHEN** an operator confirms and locks a row
- **THEN** the tool writes `status: locked` and a `source_hash` equal to its own content hash
  of the re-fetched authoritative source for that row's `sources` URL (not the corpus's
  URL-only resource identifier)

#### Scenario: Confirmation census is queryable

- **WHEN** the tool reports on the bank
- **THEN** it prints the count of `locked` vs `draft` rows and the `anchor_type` distribution,
  from the field rather than by parsing `notes`

### Requirement: Coverage-gap detection and candidate proposal

The maintenance tool SHALL compute the set of ingested KB corpus page URLs that no bank row
references in `sources`, and report them as coverage gaps. For a page an operator explicitly
greenlights, the tool SHALL draft grounded candidate questions for that page as `status:
draft`. It MUST NOT auto-add candidates for pages that were not greenlit. Because a single
source (e.g. a git repository) can contribute many per-file URLs, the report SHALL be groupable
and filterable by source (`source_type` / `parent`) so greenlighting is per-source or per-path,
not a flat list.

#### Scenario: An uncovered page appears in the gap report

- **WHEN** a corpus page URL is referenced by no bank row's `sources`
- **THEN** the tool lists that URL as a coverage gap

#### Scenario: A fully covered corpus produces no gaps

- **WHEN** every corpus page URL is referenced by at least one bank row
- **THEN** the coverage report is empty

#### Scenario: A greenlit page yields draft candidates only

- **WHEN** an operator greenlights an uncovered page
- **THEN** the tool proposes grounded candidate questions for that page, each with `status:
  draft`
- **AND** it does not lock them and does not draft candidates for pages that were not greenlit

#### Scenario: A high-volume git source is grouped, not dumped flat

- **WHEN** a git source contributes many uncovered per-file URLs
- **THEN** the coverage report groups them by their source (`source_type` / `parent`) and can be
  filtered to that one source, rather than listing every file URL flat

### Requirement: Fact-drift detection against the source page

The maintenance tool SHALL flag a `locked` row as drifted WHEN a fresh content hash it computes
over the re-fetched authoritative source for its `sources` URL differs from the row's stored
`source_hash`. It MUST hash the source itself, not the corpus's resource identifier (which is
URL-only and never reflects content change). On a mismatch the tool MUST compare the re-fetched
content against the stored `reference`, reporting the suspected staleness, and MUST NOT edit,
re-lock, or delete the row — it reports only. `draft` rows SHALL NOT be drift-checked, because
they are not yet authoritative.

#### Scenario: A changed grounding page flags the locked row

- **WHEN** a `locked` row's stored `source_hash` differs from a fresh content hash of its
  re-fetched source
- **THEN** the row is flagged as drifted with the re-fetched source for review
- **AND** the row's `reference` and `status` are left unchanged

#### Scenario: An unchanged grounding page is not flagged

- **WHEN** a `locked` row's stored `source_hash` matches a fresh content hash of its re-fetched
  source
- **THEN** the row is not flagged as drifted

#### Scenario: Draft rows are skipped by drift detection

- **WHEN** a row's `status` is `draft`
- **THEN** drift detection does not flag it, regardless of any hash comparison

### Requirement: Orphan detection for removed pages

The maintenance tool SHALL flag any bank row whose `sources` URL is absent from the ingested
corpus as an orphan (its grounding page was removed), and MUST propose it for prune or
conversion rather than deleting it. `should_refuse` rows, which intentionally carry empty
`sources`, MUST NOT be flagged as orphans.

#### Scenario: A row citing a removed page is flagged

- **WHEN** a bank row's `sources` URL is not present in the ingested corpus
- **THEN** the tool reports the row as an orphan and does not delete it

#### Scenario: A should-refuse row is never an orphan

- **WHEN** a bank row has empty `sources` (a `should_refuse` row)
- **THEN** it is not flagged as an orphan

### Requirement: Human-gated mutation

The maintenance tooling and the skill SHALL NEVER add, lock, edit, or delete a bank row
without explicit human confirmation. Every automated detection pass MUST be proposal-only: it
emits work lists and leaves the bank file byte-unchanged. Applying a proposal — adding a
drafted candidate, locking a reference, or pruning an orphan — MUST be a distinct
human-initiated action.

#### Scenario: A detection pass leaves the bank file unchanged

- **WHEN** the tool runs coverage, drift, or orphan detection
- **THEN** the bank JSON file is byte-unchanged after the pass

#### Scenario: Locking is a human action, not a side effect

- **WHEN** a reference is confirmed and locked
- **THEN** `status` flips to `locked` only through an explicit operator-invoked step, never
  automatically as a side effect of a detection pass

### Requirement: Cron-driven read-only report on the dev server

The system SHALL provide a `report` subcommand that reads the live ingested corpus and the
bank and emits a combined coverage/drift/orphan summary, suitable to run unattended from a
cron job on the dev server. It MUST be read-only with respect to both the bank file and the
corpus, and MUST reserve a non-zero exit for operational failure (e.g. the corpus is
unreachable) rather than exiting non-zero merely because gaps or drift were found.

#### Scenario: The report enumerates all three work lists

- **WHEN** `report` runs against a corpus and a bank
- **THEN** it prints coverage gaps, drift flags, and orphans
- **AND** it modifies no file

#### Scenario: Findings do not fail the cron job

- **WHEN** the report finds coverage gaps or drifted rows
- **THEN** it exits zero (a healthy detection), reserving non-zero for operational errors such
  as an unreachable corpus
