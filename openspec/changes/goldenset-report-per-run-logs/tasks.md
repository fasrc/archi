## 1. Baseline & scope confirmation

- [x] 1.1 `export PATH=/home/a2rchi/miniforge3/envs/archi/bin:$PATH`; run
      `bash scripts/benchmarking/test_goldenset_report_cron.sh` and record the passing count as
      the baseline (the gate runs this suite — `scripts/gate.sh:138`).
- [x] 1.2 Confirm nothing outside the wrapper, its self-test, and the docs depends on the fixed
      log filename: `grep -rn "goldenset-report\.log" --include='*.sh' --include='*.py'
      --include='*.md' --include='*.yaml' --include='*.yml' --include='*.service' .` — the
      installed systemd unit must not name the log (it invokes the wrapper, which derives its
      own path). If a consumer hardcodes the name, STOP and widen the change.

## 2. RED tests (write first, watch fail)

All in `scripts/benchmarking/test_goldenset_report_cron.sh`. Update the numbered contract
comment at the top of the file as the assertions change.

- [x] 2.1 Replace the "output is appended to the log, never truncated" case (currently seeds
      `PREVIOUS RUN` into `goldenset-report.log` and asserts both texts survive) with: a run
      creates exactly one new `goldenset-report-<stamp>Z.log`, and it holds the header, the
      report output, and the `===== exit` footer.
- [x] 2.2 A pre-existing earlier run's file is byte-unchanged after a new run, and the new
      output appears only in the new file.
- [x] 2.3 `goldenset-report-latest.log` resolves to the run's file; a second run repoints it;
      the earlier file still exists. Assert through the link (`cat` the symlink path), not just
      on `readlink`, and assert the target is relative.
- [x] 2.4 A misconfigured run (missing `GOLDENSET_SOURCES`, exit 2) creates no log file and
      leaves an existing `latest` symlink resolving to the previous run.
- [x] 2.5 Replace the two rotation cases (16–17: rotate past the cap, don't rotate under it)
      with: given an oversized file already in the log dir and a small
      `GOLDENSET_LOG_MAX_BYTES`, no `*.log.1` is created anywhere in the log dir.
- [x] 2.6 Fix the findings-digest assertion — the current case globs `*goldenset-report.log*`,
      which no longer matches the timestamped name. Assert the digest's `full report:` path is
      exactly the file this run wrote.
- [x] 2.7 Keep as-is: the log directory is created when absent; a single run's output is
      truncated at the cap with the marker; output under the cap carries no marker.
- [x] 2.8 Run the suite and confirm the new/changed cases FAIL for the expected reasons before
      touching the wrapper.

## 3. Implementation

`scripts/benchmarking/goldenset_report_cron.sh` only.

- [x] 3.1 Derive the run's log path from a single UTC stamp taken once:
      `RUN_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"`, then
      `LOG="$LOG_DIR/goldenset-report-$RUN_STAMP.log"`. Reuse that stamp for the in-file header
      so the filename and the banner cannot disagree.
- [x] 3.2 Delete the pre-run rotation block (the `LOG_MAX_BYTES` size check and
      `mv -f "$LOG" "$LOG.1"`). Keep `LOG_MAX_BYTES` itself — it still bounds one run's logged
      output further down.
- [x] 3.3 After the header write succeeds (so the link never points at a file that could not be
      created), refresh the pointer with a relative target:
      `ln -sfn "goldenset-report-$RUN_STAMP.log" "$LOG_DIR/goldenset-report-latest.log"`.
      `-n` so re-pointing an existing symlink replaces it instead of writing inside the
      directory it resolves to.
- [x] 3.4 Update the wrapper's own header comment: `GOLDENSET_LOG_MAX_BYTES` now caps a single
      run's logged output rather than triggering rotation; note that files accumulate by design
      and the operator prunes. Rewrite the rotation rationale comment accordingly — do not
      leave prose describing a mechanism that no longer exists.
- [x] 3.5 Run the suite green.

## 4. Docs

`docs/docs/benchmarking.md`, "Running it nightly".

- [x] 4.1 Update the `GOLDENSET_LOG_MAX_BYTES` table row: caps one run's logged output, no
      rotation.
- [x] 4.2 State the naming scheme and the `latest` symlink where the log is first introduced,
      with the one-liner an operator actually wants
      (`tail ~/.ralph/log/goldenset-report-latest.log`).
- [x] 4.3 Rewrite the rotation prose in the unattended-safety section: per-run files, per-file
      cap, unbounded file count by design, manual pruning.
- [x] 4.4 Update the rollback paragraph — it names `goldenset-report.log` as the thing that can
      be deleted or kept; it is now a directory of dated files plus a symlink.

## 5. Verify on fasrc-dev (the installed job)

- [x] 5.1 Hand-run the wrapper on the host and confirm: a new dated file appears, `latest`
      resolves to it, no `.log.1` exists, and the pre-existing `goldenset-report.log` is
      untouched.
- [x] 5.2 Confirm the run is still read-only — bank sha256 unchanged, `git status --porcelain`
      on the bank empty.
- [x] 5.3 Confirm the systemd unit needs no edit — `systemctl show -p ExecStart` names only the
      wrapper, which derives its own log path.
- [ ] 5.4 Confirm a *scheduled* run writes a dated file. Needs root to trigger
      (`sudo systemctl start goldenset-report.service`) or the 06:15 timer; the unattended path
      differs from a hand-run (no TTY, minimal environment), so this is not implied by 5.1.

## 6. Gate & PR

- [x] 6.1 `bash scripts/gate.sh` green (includes the shell self-test).
- [ ] 6.2 Commit on `feat/goldenset-per-run-logs`; PR to `fasrc/archi` base `dev`, referencing
      fasrc/archi#148 as the deploy context that motivated it.
