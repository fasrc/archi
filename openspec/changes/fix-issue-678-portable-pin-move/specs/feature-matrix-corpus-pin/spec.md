## ADDED Requirements

### Requirement: The arm-mode pin move is portable and refuses a directory target
`archive_run.sh` SHALL move the arm-mode corpus pin into place with an atomic rename that does not depend on a GNU-only command-line flag, and the move SHALL fail the archive when a directory occupies the pin path.

The script SHALL contain no `mv -T` or `mv -fT` call. When the move fails, the wrapper SHALL exit non-zero and SHALL NOT print `archived arm`.

#### Scenario: A directory appears at the pin path after the ledger append
- **WHEN** an arm-mode run-1 archive passes validation, `fm_ledger_append` succeeds, and a directory then exists at the stack's pin path
- **THEN** the wrapper exits non-zero, stdout has no `archived arm` line, and the directory is empty

#### Scenario: A normal first pin
- **WHEN** an arm-mode run-1 archive passes every check on a stack with no pin file
- **THEN** the corpus pin file holds the artifact's fingerprint and no `corpus-pin-<stack>.tmp.*` file remains

### Requirement: A directory at the pin path is refused before any ledger write
`archive_run.sh` SHALL refuse an arm-mode archive whose corpus pin path is already a directory before it appends any ledger row.

#### Scenario: A directory already at the pin path
- **WHEN** `archive_run.sh 00 1 <arm.yaml> --stack <s>` runs while `corpus-pin-<s>` is a directory
- **THEN** the wrapper exits non-zero, stderr names the directory in a `REFUSED` line, the ledger row count is the same as before the call, and no `corpus-pin-<s>.tmp.*` file exists
