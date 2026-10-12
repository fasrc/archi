## 1. Watchdog bound without timeout (red tests and fix in one task)

- [ ] 1.1 In `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`, add
      cases 83, 84, 85 and 86 from design D3 directly AFTER case 70 (the
      "without timeout on PATH the page is still sent" block) and BEFORE case 63, NOT at
      the end of the file. Build `$T/notimeout-sleep` with symlinks to `env`, `bash`,
      `cat` and `sleep`. Call `fm_page` the way case 70 does, but with
      `bash -c 'set -euo pipefail; source "$1"; fm_page …'`. Run
      `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` and
      confirm cases 83 and 84 FAIL (each takes about 30 s). Then, in the same task, change
      `fm_page` in `scripts/benchmarking/feature_matrix/lib.sh` per design D1 and D2, and
      update its header comment (the lines above `fm_page`) to describe the watchdog
      fallback and the unbounded case when `sleep` is also absent. Re-run the self-test and
      confirm `N passed, 0 failed`, with cases 69, 70, 71, 72 and 83–86 green. Run
      `bash scripts/gate.sh` and commit only when it is green (message:
      `bound fm_page mail without timeout`).

## 2. Publish

- [ ] 2.1 Push the branch: `git push -u origin fix/issue-629-fm-page-watchdog`. Open the
      PR: `gh pr create --repo fasrc/archi --base dev` with `Closes #629` in the BODY (not
      the title). In the body, give the self-test pass count and the elapsed time of
      cases 83 and 84 before and after the fix. STOP at the open PR — do not merge.
