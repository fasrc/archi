## ADDED Requirements

### Requirement: The feature-matrix wrappers SHALL page the operator by mail when a run is refused after long work

`scripts/benchmarking/feature_matrix/lib.sh` MUST provide `fm_page` and `fm_die_paged`, controlled by `FM_PAGE_MAIL_TO` (recipient; empty means off) and `FM_MAIL` (the mail binary; default `mail`). `archive_run.sh` MUST page at both "refusing to archive" exits, and `qa_arm.sh` MUST page at both "corpus changed during the QA run" exits. Each page MUST be one message whose subject starts with `feature_matrix:` and names the stack and the arm (or `sweep`). The wrapper MUST still exit 2 with the same stderr message as before.

#### Scenario: Refused archive pages once with the reason

- **WHEN** `FM_PAGE_MAIL_TO` is set and `archive_run.sh` refuses an artifact
- **THEN** the mail binary receives exactly one message addressed to `FM_PAGE_MAIL_TO`
- **AND** the subject contains `feature_matrix`, the stack name, and the arm label
- **AND** the body contains the refusal reason that the check printed to stderr
- **AND** the wrapper exits 2 and still prints the refusal to stderr

#### Scenario: QA run that fails closed pages once

- **WHEN** `FM_PAGE_MAIL_TO` is set and the corpus changes during a `qa_arm.sh` run
- **THEN** the mail binary receives exactly one message that names the stack and the arm
- **AND** no ledger row is written and the wrapper exits 2

### Requirement: Paging SHALL never change wrapper behavior when off or broken

When `FM_PAGE_MAIL_TO` is empty, the wrappers MUST NOT run the mail binary. When the mail binary fails or is missing, the wrapper MUST keep its exit code and its stderr refusal message, and MUST log `page failed`. Precondition refusals before long work (usage errors, `fm_require_*` checks, "still running", "no artifact") MUST NOT page.

#### Scenario: Paging off

- **WHEN** `FM_PAGE_MAIL_TO` is empty and `archive_run.sh` refuses an artifact
- **THEN** the mail binary is not run and the exit code and stderr are unchanged

#### Scenario: Mail binary fails

- **WHEN** `FM_PAGE_MAIL_TO` is set, the mail binary exits non-zero, and `archive_run.sh` refuses an artifact
- **THEN** the wrapper exits 2 with the same refusal message and stderr contains `page failed`

#### Scenario: Precondition refusal does not page

- **WHEN** `FM_PAGE_MAIL_TO` is set and a wrapper refuses a bad arm label
- **THEN** the mail binary is not run
