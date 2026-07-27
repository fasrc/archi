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
