## Why

`scripts/benchmarking/feature_matrix/archive_run.sh` moves the arm-mode corpus pin with
`mv -fT "$PIN_FILE.tmp.$$" "$PIN_FILE"` (line 286 on `origin/dev` `0ed50a23`). The `-T`
flag is GNU-only. BSD and macOS `mv` reject it. The move runs after `fm_ledger_append`
(line 280), so on such a host a first pin or a re-pin leaves a ledger row with no pin
(issue #678, finding 1 of the final Codex pass on PR #661).

Sweep mode already moves its pins with Python `os.replace` (`sweep_tools.py:503`), so the
two modes use different move primitives for the same file.

Self-test 81 in `test_feature_matrix_wrappers.sh` (lines 1000-1010) checks that a
directory at the pin path is refused. It checks the exit code, the `REFUSED` text, the
absence of `archived arm`, and an empty directory. It does not check the ledger. A
regression that appends the ledger row and then refuses still passes (issue #678,
finding 2).

## What Changes

- `archive_run.sh` (arm mode): replace `mv -fT` with `os.replace` through `$FM_PYTHON`
  (Option A of the issue). `os.replace` raises `IsADirectoryError` when the target is a
  directory, so the late-directory guard of self-test 82 stays in place without a GNU flag.
- `test_feature_matrix_wrappers.sh` self-test 81: assert that the ledger row count is the
  same before and after the refused archive, and that no `corpus-pin-fm-pin81.tmp.*` file
  exists.
- No change to the ledger schema, the pin format, or sweep mode.

## Capabilities

### New Capabilities

### Modified Capabilities
- `feature-matrix-corpus-pin`: the arm-mode pin move is portable (no GNU-only flag) and a
  directory at the pin path is refused before any ledger write. The capability is defined
  in the unarchived change `fix-issue-574-pin-after-ledger`, so this delta uses ADDED.

## Impact

- `scripts/benchmarking/feature_matrix/archive_run.sh` (about 3 lines).
- `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (self-test 81 and
  its header line).
- `scripts/` has no diff-coverage gate. The wrapper self-test suite, which the gate runs,
  is the only check.
