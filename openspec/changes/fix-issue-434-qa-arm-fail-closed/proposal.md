## Why

`scripts/benchmarking/feature_matrix/qa_arm.sh` records a QA run in the campaign ledger
even when the run scored nothing. Both ledger paths append unconditionally after the
corpus-drift check and then print `done`:

- sweep path: drift check `qa_arm.sh:92`, `fm_ledger_append` `:93`, `done` `:95`;
- single-arm path: drift check `qa_arm.sh:171`, `fm_ledger_append` `:172`, `done` `:175`.

(Anchors on `origin/dev` `570814d0`; the issue body quotes `0ddc96e1`, before #585 added
the `qa_identity` columns and moved the lines.)

A run whose every attempt failed writes `summary.json` with
`attempt_lifecycle_counts.scored == 0` (`src/evaluation/qa/scoring.py:213`). The ledger
row then looks like a real data point, and `compare_runs.py` joins it to an arm. Today the
only guard is an interim script in another repository
(`bench_out/feature_matrix/check_qa_scored.sh`). Issue: fasrc/archi#434.

## What Changes

- New `lib.sh` helper `fm_require_scored <out_dir>`. It reads
  `<out_dir>/summary.json` with `$FM_PYTHON` and calls `fm_die` (exit 2) when the file is
  missing, is not readable JSON, or `attempt_lifecycle_counts.scored` is absent, not a
  positive integer, or `0`. The message names the `summary.json` path and says the row was
  NOT recorded.
- `qa_arm.sh` calls `fm_require_scored "$OUT_DIR"` in both paths, right after the
  corpus-drift check and before `fm_ledger_append`.
- The hermetic wrapper self-test (`test_feature_matrix_wrappers.sh`, already run by
  `scripts/gate.sh`) gets a fake `summary.json` from the stub `archi` and new checks for
  the four cases in the issue, on both paths.

## Decision (recorded on the issue, 2026-09-26)

A failed run is `scored == 0`, a missing `summary.json`, or an unreadable one — nothing
else. A partial run (some attempts failed, `scored > 0`) is still data and is recorded.
A ratio threshold and "refuse on any failed attempt" were rejected.

## Out of scope

- A threshold on partial runs, or refusing on any failed attempt.
- `archive_run.sh` and the RAGAS leg.
- `bench_out/feature_matrix/check_qa_scored.sh` (another repository).
- `scripts/gate.sh` (it already runs the wrapper self-test).

## Impact

- `scripts/benchmarking/feature_matrix/lib.sh`, `qa_arm.sh`,
  `test_feature_matrix_wrappers.sh`. No `src/` change.
- `scripts/` is in the campaign's locked code tree (`fm_code_tree`), so a running
  feature-matrix campaign needs `--relock` after this merges and the host redeploys. The
  PR itself is safe to open.
