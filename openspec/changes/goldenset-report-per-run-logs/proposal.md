## Why

`goldenset_report_cron.sh` appends every run to one `goldenset-report.log`, rotating to
`.log.1` once past `GOLDENSET_LOG_MAX_BYTES`. That shape was chosen so a slow drift — a page
edited a little each month — stays visible as history, and so the whole thing stays bounded at
2× the cap with no logrotate unit.

Now that the job is actually installed on fasrc-dev (task 5.2a, fasrc/archi#148), the append
shape is the wrong ergonomics for the operator it was built for. Checking on a given night
means paging through one growing file to find the right `===== goldenset report <ts> =====`
banner, and a single run can be tens of thousands of lines — the first live run logged 806.
"What did last night's run say?" should be a file you open, not a region you locate.

Each run gets its own datetime-stamped file instead, plus a stable `latest` symlink so the
common case — read the most recent run — needs no glob and no timestamp arithmetic.

## What Changes

- The wrapper writes each run to `goldenset-report-<YYYYMMDD>T<HHMMSS>Z.log` in
  `GOLDENSET_LOG_DIR`. UTC, matching the in-file header stamp; no colons, so globs and
  completion stay painless; lexicographic order equals chronological order.
- The wrapper refreshes a `goldenset-report-latest.log` symlink to the file it just wrote. The
  target is relative, so the log directory can be moved or copied without dangling.
- **Removed:** the pre-run rotation block. No `.log.1` is created, ever.
- `GOLDENSET_LOG_MAX_BYTES` keeps only its second, already-implemented job: truncate a *single
  run's* logged output past that size. Each file therefore stays bounded even though the
  directory is not.
- **Retention is deliberately not implemented.** At roughly 60 KB per nightly run (~22 MB/year)
  the growth is immaterial on the dev host, and pruning would add a setting, a deletion path,
  and a way to lose history to a config typo. The operator prunes by hand. This is a decision,
  not an omission — recorded so a later reader does not "fix" it by adding a pruner.
- The existing `~/.ralph/log/goldenset-report.log` on fasrc-dev is left in place as history;
  the wrapper simply stops writing to it. No migration.
- Unchanged: the `===== goldenset report <ts> =====` / `===== exit N =====` framing (each file
  stays self-describing), log-directory creation, the three-outcome notification contract, and
  the read-only guarantee.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ragas-goldenset-maintenance`: the "Cron-driven read-only report on the dev server"
  requirement gains the log-file naming contract. The requirement lives in the unarchived
  `maintain-ragas-goldenset` change rather than in `openspec/specs/`, so this is expressed as an
  additive requirement on the same capability, in the shape
  `fix-issue-147-drift-explicit-mode` used for the same reason.

## Impact

- `scripts/benchmarking/goldenset_report_cron.sh` — log path derivation, symlink refresh,
  removal of the rotation block.
- `scripts/benchmarking/test_goldenset_report_cron.sh` — the hermetic self-test the gate runs
  (`scripts/gate.sh:138`). Three assertions change meaning, two are replaced, one is added.
- `docs/docs/benchmarking.md` — the `GOLDENSET_LOG_MAX_BYTES` table row, the rollback
  paragraph, and the rotation prose in the unattended-safety section.
- No Python, no runtime code, no deploy, no dependency changes. The installed systemd unit on
  fasrc-dev needs no edit: it invokes the wrapper, which decides its own log path.
