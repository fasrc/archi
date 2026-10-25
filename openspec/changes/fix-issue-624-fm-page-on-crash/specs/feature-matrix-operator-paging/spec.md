## ADDED Requirements

### Requirement: A feature-matrix wrapper SHALL page once on any unpaged failure after long work starts

`qa_arm.sh` MUST arm a failure page directly before its QA run, in arm mode and in sweep mode. `archive_run.sh` MUST arm a failure page directly after its "no artifact" precondition, in arm mode and in sweep mode. After that point, a non-zero exit that sent no page MUST send exactly one page whose subject starts with `feature_matrix:`, names the stack and the arm (or `sweep`), and contains the exit status. The page body MUST name the wrapper and the exit status, and MUST carry the refusal message when the exit came from `fm_die`, or else the file, the line and the command that failed when bash recorded them. A SIGHUP, SIGINT or SIGTERM to the wrapper after that point MUST also send exactly one page, and the wrapper MUST then end by that same signal. A run that already sent a page MUST NOT send a second page. The page MUST NOT change the exit status. A refusal before the arm point MUST NOT page.

#### Scenario: Fingerprint read fails after the QA run in arm mode

- **WHEN** `FM_PAGE_MAIL_TO` is set and the data-manager fingerprint read fails after `archi qa` in `qa_arm.sh` arm mode
- **THEN** the wrapper exits non-zero
- **AND** the mail binary receives exactly one message that names the stack and the arm
- **AND** no ledger row is written

#### Scenario: Fingerprint read fails after the QA run in sweep mode

- **WHEN** `FM_PAGE_MAIL_TO` is set and the data-manager fingerprint read fails after `archi qa score` in `qa_arm.sh --sweep`
- **THEN** the wrapper exits non-zero and the mail binary receives exactly one message

#### Scenario: QA scoring crashes

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archi qa score` exits 1 in `qa_arm.sh --sweep`
- **THEN** the wrapper exits non-zero and the mail binary receives exactly one message

#### Scenario: Ledger append fails in qa_arm

- **WHEN** `FM_PAGE_MAIL_TO` is set, the QA run is clean, and `fm_ledger_append` fails in `qa_arm.sh` arm mode
- **THEN** the wrapper exits 1
- **AND** the mail binary receives exactly one message whose body names `lib.sh`, the line, and the ledger-append command
- **AND** no ledger row is written

#### Scenario: Ledger append fails in archive_run

- **WHEN** `FM_PAGE_MAIL_TO` is set and `fm_ledger_append` fails during `archive_run.sh 00 1 <arm.yaml>` for a stack with no pin
- **THEN** the wrapper exits 1
- **AND** the mail binary receives exactly one message that names the stack and the arm
- **AND** no `corpus-pin-<stack>` file exists and no ledger row is written

#### Scenario: A QA run that scored nothing pages once with the reason

- **WHEN** `FM_PAGE_MAIL_TO` is set and `summary.json` of a `qa_arm.sh` arm-mode run records `scored: 0`
- **THEN** the wrapper exits 2 with the same "QA run scored nothing" refusal on stderr
- **AND** the mail binary receives exactly one message whose body carries that refusal
- **AND** no ledger row is written

#### Scenario: The wrapper's process group gets SIGTERM

- **WHEN** `FM_PAGE_MAIL_TO` is set and the process group of `qa_arm.sh` arm mode receives SIGTERM during the QA run
- **THEN** the wrapper dies of SIGTERM
- **AND** the mail binary receives exactly one message with `exit 143` in the subject
- **AND** no ledger row is written

#### Scenario: The wrapper's process group gets SIGHUP

- **WHEN** `FM_PAGE_MAIL_TO` is set and the process group of `qa_arm.sh` arm mode receives SIGHUP during the QA run
- **THEN** the wrapper dies of SIGHUP
- **AND** the mail binary receives exactly one message with `exit 129` in the subject

#### Scenario: A paged refusal does not page twice

- **WHEN** `FM_PAGE_MAIL_TO` is set and a wrapper exits through `fm_die_paged` or through the `archive_run.sh` validator refusal page
- **THEN** the mail binary receives exactly one message and the exit status is unchanged

#### Scenario: Paging off

- **WHEN** `FM_PAGE_MAIL_TO` is empty and a wrapper fails after the arm point
- **THEN** the mail binary is not run and the exit status is unchanged

#### Scenario: Precondition refusal still does not page

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archive_run.sh` refuses a bad arm label
- **THEN** the mail binary is not run

#### Scenario: No artifact still does not page

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archive_run.sh` arm mode finds no artifact for the stack
- **THEN** the wrapper exits 2 with "nothing archived" on stderr
- **AND** the mail binary is not run
