## Why

Issue #574 (PR #661) made the feature-matrix corpus pin move only after the ledger row that
records it is durable. That leaves one gap (issue #677, found by the Codex adversarial review
of PR #661): if the ledger write succeeds and the pin write after it fails (a full disk, a
permission error), the archive exits non-zero but the ledger row stays. A retry with the same
run number is then refused as a duplicate before it can write the pin:

- Arm mode, on `origin/dev` `a18b979c`: `scripts/benchmarking/feature_matrix/archive_run.sh`
  appends the row at `:301` (`fm_ledger_append "$ENTRY"`) and writes the pin at `:308-309`.
  A retry stops at the duplicate checks at `:117` (`artifact already archived as arm ...`) and
  `:120` (`arm ... run ... on ... is already archived ...; pick the next run number`).
- Sweep mode: `scripts/benchmarking/feature_matrix/sweep_tools.py` `archive()` replaces the
  ledger at `:526`, then writes `corpus-pin-<stack>` and `category-map-pin-<stack>` with two
  separate `os.replace` calls (`:532`). A retry stops at `:381` (`... is already archived`)
  and `:392` (`run ... on ... is already archived; pick the next run number`). If only the
  second pin write fails, the corpus pin is new and the category-map pin is absent.

So a first pin can stay absent, or a `--new-corpus` re-pin can stay at the old value, while
the ledger says the archive succeeded. Today the only repair is a hand-written pin file.

## What Changes

The operator decided the recovery rule on 2026-10-20 (issue #677, "Decision"): **repair on a
matching row**. When the duplicate check finds a ledger row for the same artifact (same path
and `artifact_sha256`) and the same `(arm, stack, run)`, and the pin on disk does not equal
the pin that row implies, finish the missing pin write and exit 0 with a log line that says
the pin was repaired. In sweep mode, finish both pins. Every other duplicate is still
refused.

- `archive_run.sh` (arm mode):
  - Each new ragas ledger row records `artifact_sha256`, the sha256 of the archived file
    (sweep-mode rows already record it). A row written before this change has no
    `artifact_sha256`, so it is never repaired.
  - The duplicate validator first checks for a repairable retry. It writes the pin, logs
    `repaired the corpus pin for <stack>: <fingerprint> (...)`, appends no ledger row, and
    exits 0. All other cases go to the existing refusals, which do not change.
- `sweep_tools.py` `archive()`: at the existing duplicate check, a new helper
  `_finish_pins()` writes each absent pin of a repairable run-1 retry. `archive()` prints
  `repaired <pins> for run <run> on <stack>; its ledger rows were already written` and
  returns the run's existing rows. All other cases keep the existing refusal.
- Tests: eight new self-test cases (113-120) in `test_feature_matrix_wrappers.sh` and three
  new test functions (six test items) in `tests/unit/test_sweep_tools.py`.
- Docs: `docs/docs/proposals/feature-matrix-campaign-2026.md` and
  `scripts/benchmarking/README.md` describe the retry. The #574 change's delta sentence "The
  ledger row schema SHALL not change" is scoped to that fix, because this change adds one
  arm-mode key.

### Note for the reviewer: how the decision is read

`design.md` D2 reads "the pin on disk does not equal the pin that row implies" strictly. The
repair runs only when the pin is in the state that a failed write leaves:

- after a first pin, the pin is absent;
- after a `--new-corpus` re-pin, the pin is still the row's `repinned_from` value, and the
  retry must pass `--new-corpus` again.

A pin that holds any other value (for example a hand-written pin) is not overwritten. This
keeps the existing rule that only a closing-baseline `--new-corpus` archive moves an existing
pin. With the plain "differs" reading, the existing self-test check 60 fails (measured in
a scratch copy: `108 passed, 1 failed`). The fixture re-pins `fm-00` with `--new-corpus`
(check 59), writes the old pin back by hand (`test_feature_matrix_wrappers.sh:818`), and
check 60 then expects a plain re-archive of that artifact to be refused. With the plain
reading, that re-archive silently moves the pin.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `feature-matrix-corpus-pin` (still in the unarchived changes `fix-issue-574-pin-after-ledger`
  and `fix-issue-678-portable-pin-move`, so this delta uses `ADDED`): a retry finishes a pin
  write that failed after its ledger row was written.

## Impact

- Code: `scripts/benchmarking/feature_matrix/archive_run.sh`,
  `scripts/benchmarking/feature_matrix/sweep_tools.py`. Nothing under `src/`, so diff-cover
  scores no lines; the self-test (run by the gate) and `tests/unit/test_sweep_tools.py` are the
  guard.
- Ledger: arm-mode ragas rows gain the key `artifact_sha256` (additive; no reader checks the
  exact key set).
- Related: open PR #782 (#624) also edits `test_feature_matrix_wrappers.sh` and adds cases
  98-112, so this change numbers its cases from 113. A rebase over #782 must keep both blocks.
