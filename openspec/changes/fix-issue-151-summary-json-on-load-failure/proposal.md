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

Round 2 (Codex review on PR #162) — the consequences of putting a *write* on a
path that previously only read:

- **Refuse an output path that names an input.** `--summary-json` (or
  `--ledger`) pointed at `--bank`/`--corpus-json`/`--sources` destroys that
  input at the `os.replace` commit point. Checked in `main` before dispatch, by
  file identity rather than string equality.
- **Reject a non-text `anchor_type` before the census**, rather than letting it
  crash the sort after the bank handler has been passed (or, when it does not
  crash, report a bucket that is not in the bank).
- **Preserve the target's access metadata** — owner/group and POSIX ACL as well
  as mode — across both replace-in-place writes.
- **Do not overclaim the file.** A write that itself fails leaves the previous
  summary in place, so file-only monitoring cannot see a failed refresh.

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
- Consumers: an external monitor gets one fewer stale-success state (a bank that
  failed to load), but the file is **not** self-certifying — a failed refresh
  leaves the previous file in place — so the exit code and the file's freshness
  remain required. The `goldenset_report_cron.sh` wrapper (which keys on the
  exit code) is unaffected.
- Operators pointing `--summary-json` or `--ledger` at a file the same run reads
  now get a refusal at startup instead of a destroyed input.
- No runtime/deployment dependency; verified with the local gate.
