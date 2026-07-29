## Why

`goldenset_maintenance.py report` documents (and its own code comment at
`run_report` asserts) that `--summary-json` is "written on every path, including
the failing one," so an external monitor can key on the file alone. That
guarantee does **not** hold when the question bank itself fails to load: the run
raises before the summary dict is created, `main` returns non-zero, and any
previous run's summary file is left in place — stale. A monitor built to the
documented "read the summary" contract would read a stale healthy summary as
healthy after a broken startup. Surfaced by the adversarial review on #150.

## What Changes

- Initialize the report summary dict with a **failed-startup marker** *before*
  `load_bank`, so the write path always has a summary to emit.
- Wrap the bank load in `try/except OperationalError`; on failure record
  `failed_passes = ["bank: <error>"]`, `census = null`, `notify = true`, write
  the summary, then re-raise so `main` still returns non-zero.
- Route the summary write through a small **atomic** helper (temp file +
  `os.replace`), matching `write_ledger`, and use it on both the normal and
  the startup-failure paths (today's normal write is a plain, non-atomic
  `open(..., "w")`).
- Reconcile the code comment at `run_report` and the "summary JSON" subsection
  of `docs/docs/benchmarking.md` so the "written on every path" guarantee is
  true unconditionally.
- No change to the exit code semantics: a startup failure still exits non-zero.

## Capabilities

### New Capabilities
- `goldenset-report-summary`: the `report` subcommand's `--summary-json` output
  contract — the summary file is written on every terminating path, including a
  bank-load (startup) failure, and a startup failure produces a summary that
  says "broke" (never a stale success), written atomically.

### Modified Capabilities
<!-- None: no existing capability spec covers the goldenset maintenance report summary contract. -->

## Impact

- `scripts/benchmarking/goldenset_maintenance.py` — `run_report`, plus a new
  small atomic-write helper for the summary (or reuse of the existing atomic
  pattern from `write_ledger`).
- `docs/docs/benchmarking.md` — the "summary JSON" subsection, reconciled to the
  now-unconditional guarantee.
- Consumers: an external monitor may now rely on the summary file alone as a
  health signal; the `goldenset_report_cron.sh` wrapper (which keys on the exit
  code) is unaffected.
- No runtime/deployment dependency; verified with the local gate.
