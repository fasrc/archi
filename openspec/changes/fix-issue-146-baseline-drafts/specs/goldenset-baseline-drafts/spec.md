## ADDED Requirements

### Requirement: Baseline draft rows on demand

The `drift` detector SHALL provide an opt-in mode that fetches and hashes the sources of `draft` golden-set rows so their `source_hashes` baseline can be computed before the row is locked. When the mode is disabled (the default), the detector's behavior is unchanged and `draft` rows are not fetched or hashed.

#### Scenario: Draft row produces a fresh digest when baselining is requested

- **WHEN** `find_drift` is invoked with draft baselining enabled and the bank contains a `draft` row with sources
- **THEN** that row's sources are fetched and hashed, and the resulting `source_hashes` digest is exposed on the drift report as a baseline-only result

#### Scenario: Draft rows are untouched when baselining is not requested

- **WHEN** `find_drift` is invoked without draft baselining enabled
- **THEN** no `draft` row's sources are fetched or hashed, matching the pre-existing behavior

### Requirement: Draft baselining never subjects a draft row to drift detection

A `draft` row pulled in for baselining SHALL contribute a hash block and nothing else. It MUST NOT be treated as a drift finding, a missing-baseline finding, or an LLM-judged row, and MUST NOT affect any drift-detection aggregate. This invariant SHALL be expressed structurally — baseline-only rows are carried on a dedicated field of the drift report, separate from drift findings — rather than as conditionals scattered across the detection path.

#### Scenario: Baselined draft row is excluded from every drift-detection outcome

- **WHEN** a `draft` row is baselined during a `find_drift` run
- **THEN** it does NOT appear in `report.drifted`
- **AND** it does NOT appear in `report.unbaselined`
- **AND** it does NOT trigger an LLM call
- **AND** it does NOT count toward `checked_rows`
- **AND** it does NOT change the abstention decision

#### Scenario: A baselined draft is still counted as a skipped row

- **WHEN** the same one-`draft` bank is run with draft baselining off and then on
- **THEN** `report.skipped_rows` is `1` in both runs
- **AND** the count is unchanged by the flag, because the row was excluded from drift
  checking either way and `skipped_rows` is the total that accounts for exactly that
- **AND** this is the deliberate exception to the exclusion above: excluding a baselined
  draft from `skipped_rows` too would let an output-only flag move a detection metric, so
  a one-draft bank would report `0 skipped` while skipping one

### Requirement: An incomplete draft baseline block is labelled, never silently partial

A printed draft block SHALL name every source of that row which produced no hash this run —
unreachable, refused by the allowlist, or not a parseable URL — and SHALL label the block
`INCOMPLETE`. The printed block is *pasted*, and pasting replaces the row's whole
`source_hashes` map; a locked row can carry a failed source's stored hash forward, but a
draft has none, so a source omitted from a draft block is simply absent once the row is
locked. A draft whose every source failed SHALL still be listed and named rather than
skipped, because silence on a row the operator asked about directly reads as "nothing to
do" rather than "nothing could be read".

#### Scenario: A partially-hashable draft block is labelled INCOMPLETE

- **WHEN** a `draft` row cites two sources and one of them cannot be fetched
- **THEN** the emitted block contains the digest of the source that was read
- **AND** the output is labelled `INCOMPLETE` and names the source that was not
- **AND** the run still exits `0`, because an unreachable page is a fact about the KB, not
  a tool failure

#### Scenario: A fully-hashable draft block carries no warning

- **WHEN** every source of a `draft` row is fetched successfully
- **THEN** the emitted block is not labelled `INCOMPLETE`
- **AND** the label therefore continues to carry information rather than decorating every
  block

#### Scenario: A draft whose every source failed is named, not silent

- **WHEN** a `draft` row's only source cannot be fetched
- **THEN** the row is still listed in the output and named `INCOMPLETE`
- **AND** no `source_hashes` block is printed for it, because there is nothing to paste

### Requirement: Draft baselining requires hash printing

The `drift` CLI SHALL reject `--baseline-drafts` supplied without `--print-hashes`, exiting
non-zero before any source is fetched. Alone, the flag computes digests that nothing emits,
turning an ordinary drift check into one outbound request per draft source in exchange for no
output. The rejection SHALL NOT be implemented by implying `--print-hashes`, which would
silently widen the run to every locked row's block as well.

#### Scenario: The flag combination is rejected before fetching

- **WHEN** the operator runs the `drift` CLI with `--baseline-drafts` and no `--print-hashes`
- **THEN** the command exits `2` and names `--print-hashes` on stderr
- **AND** no source is fetched, so the rejection costs nothing rather than aborting after the
  requests have already been made

### Requirement: The drift tool never writes the bank file

The `drift` command SHALL remain read-only with respect to the golden-set bank file, including when draft baselining is used. Recording a baseline remains a human edit performed by pasting the printed `source_hashes` block.

#### Scenario: Bank file is byte-identical after a print-hashes run that includes drafts

- **WHEN** the operator runs the `drift` CLI with the draft-baselining flag together with `--print-hashes`
- **THEN** a paste-ready `source_hashes` block is emitted for the `draft` row(s)
- **AND** the golden-set bank file is byte-for-byte unchanged by the run

### Requirement: CLI guides the single-edit lock workflow

The `drift` CLI SHALL guide operators toward computing a row's baseline before locking, so that locking a row is a single edit (set `status: locked` and paste `source_hashes` together). It MUST NOT instruct the operator to lock a row first and then re-run to obtain its hash.

#### Scenario: No lock-first guidance remains

- **WHEN** the `drift` CLI has nothing to emit for locked rows
- **THEN** its guidance points at the draft-baselining flag
- **AND** it does not tell the operator to set `status: locked` and re-run to obtain the block
