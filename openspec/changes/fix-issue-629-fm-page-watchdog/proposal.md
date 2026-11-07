## Why

`fm_page` in `scripts/benchmarking/feature_matrix/lib.sh` bounds the mail command with
`timeout -k 5 "$secs"` only when `timeout` is on `PATH`. When `timeout` is absent, `bound`
stays empty and `$FM_MAIL` runs with no time limit (issue #629, found by a Codex review of
PR #621). On such a host (for example a default macOS shell), a hung mail command blocks
`archive_run.sh` and `qa_arm.sh` before their refusal exit (exit 2) for as long as the mail
command hangs. Commit `79e2a564` added the unbounded fallback so that a missing `timeout`
does not drop a page; that property must stay.

## What Changes

- When `timeout` is absent and `sleep` is on `PATH`, `fm_page` runs the mail command in the
  background under a bash watchdog: after `FM_MAIL_TIMEOUT` seconds the watchdog sends
  SIGTERM, and 5 seconds later SIGKILL. A killed or non-zero mail command logs
  `page failed`. `fm_page` still returns 0 in all cases.
- When the mail command returns before the deadline, `fm_page` stops the watchdog and its
  `sleep` child, so no process outlives `fm_page`.
- When `timeout` and `sleep` are both absent, the page is still sent, unbounded (case 70
  keeps this). No new dependency (no `perl`, no `python`).
- The `timeout` path (cases 69, 71, 72) does not change.
- The `fm_page` header comment describes the fallback.
- New self-test cases in `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`.

## Capabilities

### New Capabilities

- `feature-matrix-operator-paging`: adds one requirement for the bound on a host without
  `timeout`. The capability spec is not archived yet (it lives in the unarchived change
  `fix-issue-504-fm-page-mail`), so this change uses ADDED, not MODIFIED.

### Modified Capabilities

None.

## Impact

- `scripts/benchmarking/feature_matrix/lib.sh` (`fm_page` only).
- `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (new cases, put
  after case 70 and before case 63, not at the end of the file).
- `scripts/gate.sh:141` already runs this self-test. `scripts/` has no Python coverage, so
  the self-test is the only check.
