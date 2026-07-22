## ADDED Requirements

### Requirement: Machine-queryable confirmation state

Each bank row SHALL carry a `status` of `draft` or `locked`. This confirmation state governs
the **maintenance tooling only**: the tool MUST NOT treat a row as authoritative for drift or
census unless it is `locked`. Benchmark eligibility and scoring are unchanged — the harness
MUST continue to score every row's `reference` regardless of `status`; gating scoring on
`locked` is a separate, deferred change (see proposal). A `locked` row that cites one or more
`sources` SHALL record `source_hashes` — a mapping from each normalized `sources` URL to the
content hash of that grounding page captured at lock time. A `locked` row with empty `sources`
(e.g. a confirmed `should_refuse` anchor) SHALL be lockable with **no** `source_hashes` and is
simply never drift-checked. These fields SHALL be loader-compatible: the benchmark harness
requires only `user_input` (plus `sources` for SOURCES mode) and MUST continue to load banks
whether or not the fields are present.

#### Scenario: Absent status is treated as draft by the maintenance tooling

- **WHEN** a bank row has no `status` field
- **THEN** the maintenance tooling treats it as `draft` (not authoritative for drift or census)
- **AND** the benchmark harness loads and scores it exactly as before this change

#### Scenario: Locking a multi-source row records a hash per source URL

- **WHEN** an operator confirms and locks a row that cites one or more `sources`
- **THEN** the tool writes `status: locked` and a `source_hashes` map with one entry per
  normalized `sources` URL, each equal to the tool's own content hash of the re-fetched
  authoritative source for that URL (not the corpus's URL-only resource identifier)

#### Scenario: Locking a source-less refusal row records no hash

- **WHEN** an operator confirms and locks a `should_refuse` row whose `sources` is empty
- **THEN** the tool writes `status: locked` with no `source_hashes` entry
- **AND** drift detection never flags it (there is no grounding page to compare)

#### Scenario: Confirmation census is queryable

- **WHEN** the tool reports on the bank
- **THEN** it prints the count of `locked` vs `draft` rows and the `anchor_type` distribution,
  from the field rather than by parsing `notes`

### Requirement: Coverage-gap detection and candidate proposal

The maintenance tool SHALL compute the set of ingested KB corpus page URLs that no bank row
references in `sources`, and report them as coverage gaps. Before classifying, it MUST reconcile
corpus URLs against row `sources` URLs with **slug-aware** normalization (not only scheme and
trailing slash), because the sitemap-driven ingest may store a page under a different slug than
the bank's authored canonical URL; a corpus URL that resolves to a covered page only by a
near-miss MUST be reported in a separate "needs reconciliation" bucket, never classified as a
definitive gap. Whether a page is *covered* SHALL be re-derived from the current bank on every
run (a URL is covered iff some current bank row's `sources` references it after reconciliation);
the tool MUST NOT treat a URL as covered merely because candidates were once drafted for it. For
a page an operator explicitly greenlights, the tool SHALL draft grounded candidate questions for
that page as `status: draft`. It MUST NOT auto-add candidates for pages that were not greenlit.
Because a single source (e.g. a git repository) can contribute many per-file URLs, the report
SHALL be groupable and filterable by source (`source_type` / `parent`) so greenlighting is
per-source or per-path, not a flat list. The tool SHALL persist a decision ledger of URLs an
operator has explicitly **declined**, and MUST NOT resurface a declined URL as a gap; a
greenlit-but-not-yet-applied page (candidates drafted, no bank row added) MUST still appear as a
gap until a bank row actually covers it.

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

#### Scenario: A previously declined page does not resurface

- **WHEN** an operator explicitly declined an uncovered page on an earlier run
- **THEN** a later coverage run does not list it as a new gap, because the decline is recorded in
  the decision ledger

#### Scenario: A greenlit-but-unapplied page stays a gap

- **WHEN** an operator greenlit a page and candidates were drafted, but no bank row citing that
  URL has been applied yet
- **THEN** a later coverage run still lists that URL as a gap, because covered-ness is re-derived
  from the current bank rather than from the fact that candidates were once drafted

#### Scenario: A slug near-miss is reported separately, not as a gap

- **WHEN** a corpus URL matches a bank row's `sources` only after slug reconciliation (a
  near-miss, not an exact normalized match)
- **THEN** the tool reports it in the "needs reconciliation" bucket, not as a coverage gap or an
  orphan

### Requirement: Fact-drift detection against the source page

The maintenance tool SHALL flag a `locked` row as drifted WHEN, for **any** of its `sources`
URLs, a fresh content hash it computes over the re-fetched authoritative source differs from that
URL's entry in the row's stored `source_hashes`. It MUST hash each source itself, not the
corpus's resource identifier (which is URL-only and never reflects content change), and MUST
check every URL in `sources`, not only the first. On a mismatch the tool MUST compare the
re-fetched content against the stored `reference`, reporting the suspected staleness and which
source URL moved, and MUST NOT edit, re-lock, or delete the row — it reports only. `draft` rows,
and `locked` rows with empty `sources`, SHALL NOT be drift-checked.

#### Scenario: A changed grounding page flags the locked row

- **WHEN** any of a `locked` row's `sources` URLs has a fresh content hash differing from its
  stored `source_hashes` entry
- **THEN** the row is flagged as drifted, naming the changed source URL, with the re-fetched
  content for review
- **AND** the row's `reference` and `status` are left unchanged

#### Scenario: An unchanged grounding page is not flagged

- **WHEN** every one of a `locked` row's `sources` URLs matches its stored `source_hashes` entry
- **THEN** the row is not flagged as drifted

#### Scenario: Draft and source-less rows are skipped by drift detection

- **WHEN** a row's `status` is `draft`, or it is `locked` with empty `sources`
- **THEN** drift detection does not flag it, regardless of any hash comparison

### Requirement: Orphan detection for removed pages

The maintenance tool SHALL flag a bank row whose `sources` URL no longer exists in the live KB
as an orphan (its grounding page was removed), and MUST propose it for prune or conversion
rather than deleting it. Because the ingested corpus upserts by URL hash and does **not** prune
pages that disappear from a later sitemap/source list (a plain re-ingest keeps stale rows; see
design D2/D6), absence-from-corpus alone is NOT sufficient evidence of removal. The tool MUST
therefore key orphan detection on a **freshly expanded live source inventory** (the current
sitemap / source-list expansion), or require an explicit corpus prune/nuke before treating
corpus presence as evidence a page still exists. A URL that matches only by slug near-miss MUST
be treated as reconciliation-needed, not an orphan. `should_refuse` rows, which intentionally
carry empty `sources`, MUST NOT be flagged as orphans.

#### Scenario: A row citing a removed page is flagged even if the stale corpus still holds it

- **WHEN** a bank row's `sources` URL is absent from a freshly expanded live source inventory
- **THEN** the tool reports the row as an orphan and does not delete it, even if a stale corpus
  row for that URL still exists

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
