## ADDED Requirements

### Requirement: A feature-matrix wrapper SHALL page once on any unpaged failure after long work starts

`qa_arm.sh` MUST arm a failure page directly before its QA run, in arm mode and in sweep mode. `archive_run.sh` MUST arm a failure page directly after its "no artifact" precondition, in arm mode and in sweep mode. After that point, a non-zero exit that sent no page MUST send exactly one page whose subject starts with `feature_matrix:`, names the stack and the arm (or `sweep`), and contains the exit status. A SIGHUP, SIGINT or SIGTERM to the wrapper after that point MUST also send exactly one page. A run that already sent a page MUST NOT send a second page. The page MUST NOT change the exit status. A refusal before the arm point MUST NOT page.

#### Scenario: Fingerprint read fails after the QA run in arm mode

- **WHEN** `FM_PAGE_MAIL_TO` is set and the data-manager fingerprint read fails after `eval qa` in `qa_arm.sh` arm mode
- **THEN** the wrapper exits non-zero
- **AND** the mail binary receives exactly one message that names the stack and the arm
- **AND** no ledger row is written

#### Scenario: Fingerprint read fails after the QA run in sweep mode

- **WHEN** `FM_PAGE_MAIL_TO` is set and the data-manager fingerprint read fails after `eval qa score` in `qa_arm.sh --sweep`
- **THEN** the wrapper exits non-zero and the mail binary receives exactly one message

#### Scenario: QA scoring crashes

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archi eval qa score` exits 1 in `qa_arm.sh --sweep`
- **THEN** the wrapper exits non-zero and the mail binary receives exactly one message

#### Scenario: Ledger append fails in qa_arm

- **WHEN** `FM_PAGE_MAIL_TO` is set, the QA run is clean, and `fm_ledger_append` fails in `qa_arm.sh`
- **THEN** the wrapper exits non-zero and the mail binary receives exactly one message

#### Scenario: The wrapper itself is killed by a signal

- **WHEN** `FM_PAGE_MAIL_TO` is set and `qa_arm.sh` receives SIGTERM during the QA run
- **THEN** the wrapper exits 143 and the mail binary receives exactly one message

#### Scenario: A paged refusal does not page twice

- **WHEN** `FM_PAGE_MAIL_TO` is set and a wrapper exits through `fm_die_paged` or through the `archive_run.sh` validator refusal page
- **THEN** the mail binary receives exactly one message and the exit status is unchanged

#### Scenario: Paging off

- **WHEN** `FM_PAGE_MAIL_TO` is empty and a wrapper fails after the arm point
- **THEN** the mail binary is not run and the exit status is unchanged

#### Scenario: Precondition refusal still does not page

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archive_run.sh` refuses a bad arm label
- **THEN** the mail binary is not run

### Requirement: The feature-matrix archive SHALL write a corpus pin only after its ledger row is written

`archive_run.sh` arm mode MUST write the corpus pin only after `fm_ledger_append` succeeds, and only when no pin existed or the entry records `repinned_from`. The pin write MUST replace the pin file atomically. `sweep_tools.py archive` MUST write the corpus pin and the category-map pin only after the ledger file is replaced. A failed ledger write MUST leave no new or changed pin.

#### Scenario: Ledger write fails on run 1 in arm mode

- **WHEN** `FM_PAGE_MAIL_TO` is set and the ledger write fails during `archive_run.sh 00 1 <arm.yaml>` for a stack with no pin
- **THEN** the wrapper exits non-zero
- **AND** the mail binary receives exactly one message
- **AND** no `corpus-pin-<stack>` file exists

#### Scenario: Ledger write fails on run 1 in sweep mode

- **WHEN** the ledger replace fails during `sweep_tools.py archive` for run 1
- **THEN** the archive raises and neither the corpus pin nor the category-map pin exists

#### Scenario: Successful run 1 still writes the pin

- **WHEN** `archive_run.sh 00 1 <arm.yaml>` archives a valid artifact for a stack with no pin
- **THEN** the ledger has the new row and `corpus-pin-<stack>` holds the row's `corpus_fingerprint`
