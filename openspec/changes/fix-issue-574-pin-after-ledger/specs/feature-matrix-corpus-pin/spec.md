## ADDED Requirements

### Requirement: The corpus pin moves only after its ledger row is written
`archive_run.sh` SHALL write or move a stack's corpus pin only after the ledger write that records that archive succeeded, in arm mode and in sweep mode.

A pin write SHALL be atomic: the pin file holds the old value or the new value, never a partial value. When the ledger write fails, the pin file SHALL keep its value from before the run (or stay absent if it was absent), and the wrapper SHALL exit non-zero. The ledger row schema SHALL not change.

#### Scenario: First pin with a failed ledger append
- **WHEN** `archive_run.sh <arm> 1 <arm.yaml>` runs on a stack with no pin file and `fm_ledger_append` fails
- **THEN** the wrapper exits non-zero and no corpus pin file exists for the stack

#### Scenario: Closing-baseline re-pin with a failed ledger append
- **WHEN** `archive_run.sh 00 <run> <arm.yaml> --new-corpus` passes every check for a new fingerprint and `fm_ledger_append` fails
- **THEN** the wrapper exits non-zero and the corpus pin file still holds the previous pin

#### Scenario: Successful archive still writes the pin
- **WHEN** an arm-mode archive passes every check and `fm_ledger_append` succeeds
- **THEN** the ledger has the new row and the corpus pin file holds the artifact's fingerprint

#### Scenario: Sweep run 1 with a failed ledger write
- **WHEN** `sweep_tools.py archive` runs for run 1 and the ledger write raises
- **THEN** neither the corpus pin file nor the category-map pin file for the stack exists
