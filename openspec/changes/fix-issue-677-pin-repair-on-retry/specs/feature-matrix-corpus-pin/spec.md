## ADDED Requirements

### Requirement: A retry finishes a pin write that failed after its ledger row was written
`archive_run.sh` SHALL finish the pin write, append no ledger row, log a line that says the pin was repaired, and exit 0 when a retry finds that the archive's ledger rows are durable and the pin is in the state that a failed pin write leaves; it SHALL refuse every other duplicate as before.

The retry is repairable only when ALL of these are true:

- The stack's newest `kind: ragas` ledger row (in sweep mode, the newest rows) records the same artifact path, the same `artifact_sha256` (computed again from the file) and the same run (in arm mode, also the same arm).
- Arm mode: the pin file is absent and the row has `repinned_from: null`; or the pin file holds the row's `repinned_from` value and the retry passes `--new-corpus`. The repair writes the row's `corpus_fingerprint`.
- Sweep mode: the rows record `census_map_digest` (run 1 only). Each pin that exists already holds its value (`corpus_fingerprint` for `corpus-pin-<stack>`, `census_map_digest` for `category-map-pin-<stack>`), and at least one pin is absent. The repair writes each absent pin.

Each new arm-mode ragas row SHALL record `artifact_sha256`, the sha256 hex digest of the archived file. A row with no `artifact_sha256` is never repaired.

#### Scenario: Arm-mode retry after a failed first-pin write
- **WHEN** an arm-mode run-1 archive appended its ledger row, its pin write failed, and `archive_run.sh 00 1 <arm.yaml> --stack <s>` runs again
- **THEN** the wrapper exits 0, stdout has `repaired the corpus pin for <s>` and no `archived arm` line, the pin holds the artifact's fingerprint, and the ledger has the same number of rows as before the retry

#### Scenario: A second archive of a complete arm-mode run is refused
- **WHEN** the pin of that run holds its fingerprint and the same archive runs again
- **THEN** the wrapper exits non-zero with `already archived` on stderr, and the ledger and the pin do not change

#### Scenario: Arm-mode rows that cannot prove the retry are refused
- **WHEN** the pin is absent and the matching row has no `artifact_sha256`, or a later ragas row exists for the stack, or the artifact's bytes changed since the row was written
- **THEN** the wrapper exits non-zero with `already archived` on stderr and writes no pin

#### Scenario: A pin that holds another value is kept
- **WHEN** the matching row has `repinned_from: null` and the pin file holds a value other than the row's fingerprint
- **THEN** the wrapper exits non-zero with `already archived` on stderr and the pin keeps that value

#### Scenario: A failed re-pin is finished only with --new-corpus
- **WHEN** an arm-00 `--new-corpus` re-pin appended its ledger row, its pin write failed, and the same archive runs again without `--new-corpus`
- **THEN** the wrapper exits non-zero with `already archived` on stderr and the pin keeps its old value
- **WHEN** the same archive runs again with `--new-corpus`
- **THEN** the wrapper exits 0 with `repaired the corpus pin` on stdout and the pin holds the new fingerprint

#### Scenario: Sweep run-1 retry after a failed pin replace
- **WHEN** `sweep_tools.py archive` for run 1 replaced the ledger and the `os.replace` of `corpus-pin-<stack>` (or only of `category-map-pin-<stack>`) failed, and `archive()` runs again for run 1
- **THEN** it returns the run's rows, prints `repaired` with the names of the pins it wrote, both pins hold the values of the rows, and the ledger keeps the same rows

#### Scenario: Sweep retries that are not a failed pin write are refused
- **WHEN** run 1 is complete, or a later ragas row exists for the stack, or the rows have no `census_map_digest`, or a pin holds another value
- **THEN** `archive()` raises `SweepError` with `already archived` and writes no pin
