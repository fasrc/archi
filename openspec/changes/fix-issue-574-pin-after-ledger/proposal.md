## Why

`scripts/benchmarking/feature_matrix/archive_run.sh` moves the corpus pin before it writes
the ledger row that records the move (issue #574). On `origin/dev` `6156b760` the arm-mode
validator writes the pin inside its Python block (`open(pin_file, "w")` at lines 231 and
233, the `--new-corpus` branch and the first-pin branch). The shell appends the ledger row
later (`fm_ledger_append "$ENTRY"` at line 267).

If anything fails between the two (ledger read, JSON build, a full disk), the pin has moved
and no ledger row records `repinned_from` or the closing baseline that authorized the move.
Later runs then pass the pin check against an unrecorded pin.

Sweep mode (`archive_run.sh --sweep`) has the same order. `sweep_tools.py` `archive()` writes
the corpus pin and the category-map pin at lines 494-496, before the ledger `os.replace` at
line 500. A failed ledger write leaves both run-1 pins with no ledger rows.

The defect was found by the Codex adversarial review on #570. It predates #570.

## What Changes

- `archive_run.sh` (arm mode): the Python validator no longer writes the pin. It reports the
  new pin value (empty when the pin does not move) next to the ledger entry. After
  `fm_ledger_append` succeeds, the shell writes the pin atomically (a temp file in the same
  directory, then `mv`).
- `sweep_tools.py` `archive()`: write the two run-1 pins only after the ledger
  `os.replace` succeeds, each one atomically (temp file + `os.replace`).
- `test_feature_matrix_wrappers.sh`: new checks. A forced ledger-append failure leaves the
  old pin in place (first pin: no pin file; `--new-corpus`: the previous pin value), and
  the wrapper exits non-zero.
- `tests/unit/test_sweep_tools.py`: a failed ledger write on run 1 leaves no pin files.

No change to the ledger row schema, to the refusal messages, or to paging.

## Capabilities

### New Capabilities

- `feature-matrix-corpus-pin`: the corpus pin moves only after the ledger row that records
  the move is written.

### Modified Capabilities

None.

## Impact

- Code: `scripts/benchmarking/feature_matrix/archive_run.sh`,
  `scripts/benchmarking/feature_matrix/sweep_tools.py`.
- Tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (run by
  `scripts/gate.sh:141`), `tests/unit/test_sweep_tools.py`.
- Related: #624 plan step 4 asks for the same arm-mode change. This change lands that piece
  first; #624 keeps the page-on-crash work.
