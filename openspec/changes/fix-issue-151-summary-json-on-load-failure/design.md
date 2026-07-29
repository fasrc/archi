## Context

`scripts/benchmarking/goldenset_maintenance.py report` is the read-only nightly
health check over the golden-set question bank. When invoked with
`--summary-json <path>`, it emits a machine-readable summary so an external
monitor can read the file and decide whether to alert.

Current control flow in `run_report` (dev @ 0604fc4a):

- `:1014` `census = bank_status_counts(load_bank(args.bank))` — the bank is
  loaded **first**, before the summary dict exists.
- `:1015` the `summary` dict is initialized.
- `:1061-1069` the summary is written with a plain, non-atomic
  `open(args.summary_json, "w")`.
- `main` (`:1312`) calls `run_report` inside `try/except OperationalError` and
  returns 1 on failure.

`load_bank` raises `OperationalError` for a missing, unreadable, or malformed
bank. Because that raise happens at `:1014` — before the summary dict is created
and before the write block — `main` returns 1 without ever writing the summary.
Any previous run's summary file is left untouched: **stale**. The comment at
`:1062` ("Written on every path, including the failing one") is therefore only
true for *pass* failures, which are reached after the bank loads.

`write_ledger` (`:250`) is the existing atomic-write pattern in this file: write
to a same-directory temp file, flush + fsync, `os.replace` over the target,
fsync the parent directory, and remove the temp file on any failure.

## Goals / Non-Goals

**Goals:**
- Guarantee `--summary-json` is written on the bank-load (startup) failure path,
  not just the pass-failure paths.
- Make a startup failure produce a summary that says "broke" (`failed_passes`
  names the bank error, `census = null`, `notify = true`), never a stale success.
- Make the summary write atomic (temp + `os.replace`), matching `write_ledger`,
  on both the normal and the startup-failure paths.
- Reconcile the `:1062` comment and the "summary JSON" docs subsection so the
  guarantee is unconditionally true.
- Preserve exit-code semantics: a startup failure still exits non-zero.

**Non-Goals:**
- Changing what triggers `notify` on the *pass-failure* paths (`_NOTIFY_ON`).
- Changing `load_bank`, `bank_status_counts`, or the report passes themselves.
- Full fsync/durability parity with `write_ledger`'s `LedgerNotDurable`
  distinction — atomicity (no torn write) is the requirement here, not the
  before/after-commit error taxonomy of the ledger.
- Any change to `goldenset_report_cron.sh` (it keys on the exit code and is
  already safe).

## Decisions

**Decision: Initialize the summary dict before `load_bank`, seeded as failed.**
Move (or duplicate) the summary dict initialization above the `load_bank` call
and seed it with a failed-startup marker: `census = None`,
`failed_passes = []`, `notify = True` — the "broke" default. Then wrap the load:

```
try:
    census = bank_status_counts(load_bank(args.bank))
    summary["census"] = census
    summary["notify"] = False   # cleared; recomputed after passes
except OperationalError as exc:
    summary["failed_passes"] = [f"bank: {exc}"]
    summary["census"] = None
    summary["notify"] = True
    _write_summary(args, summary)   # write BEFORE re-raising
    raise
```

*Why:* the write must be reachable on the failure path. Seeding the dict as
"broke" and only clearing `notify` after a successful load means a half-built
summary is safe to emit at any point. Re-raising preserves `main`'s non-zero
exit and its stderr message.

*Alternative considered:* have `run_report` catch and `return 1` itself instead
of re-raising. Rejected — re-raising keeps the single `OperationalError` →
stderr + exit-1 funnel in `main`, so the error message format stays identical to
every other operational failure.

**Decision: Extract an atomic `_write_summary(args, summary)` helper.**
Factor the existing write block into a helper that writes to a same-directory
temp file and `os.replace`s it over the target (mirroring `write_ledger`'s
commit step), preserving the current `OperationalError("cannot write ...")` on
`OSError`. Call it from both the normal path (replacing the `:1061-1069` block)
and the startup-failure `except`.

*Why:* today's normal write is a plain `open(..., "w")`, which truncates first —
a crash mid-write would leave a mangled summary, exactly the stale/ambiguous
state this issue exists to remove. One helper keeps both paths identical and
satisfies "keep the write atomic, matching `write_ledger`."

*Alternative considered:* leave the normal write plain and only make the failure
path atomic. Rejected — inconsistent, and the normal path has the same
torn-write hazard.

**Decision: Reconcile the comment and docs to the now-unconditional guarantee.**
Update the `:1062` comment to state the summary is written on every terminating
path including a bank-load failure, and update the "summary JSON" subsection of
`docs/docs/benchmarking.md` to match. `#150` documented the defensive contract
("treat non-zero exit or a stale/missing summary as failure"); with this change
the file-alone signal is reliable, so the docs should say the summary is always
current, while still noting the exit code as the authoritative signal.

## Risks / Trade-offs

- **Risk: `notify` is left `true` from the seed if a later code path forgets to
  clear it on success.** → The success path recomputes `notify` from `_NOTIFY_ON`
  after the passes run (unchanged today); the seed only governs the pre-load
  window. A test asserts a healthy run still emits `notify` per the existing
  rule.
- **Risk: temp-file litter or wrong-directory temp on failure.** → Mirror
  `write_ledger`: create the temp file in the target's directory and remove it
  in a `finally`/`except` on any failure. Covered by the atomicity scenario.
- **Risk: `census = None` breaks a downstream consumer expecting a dict.** →
  This is the intended failure signal (a startup failure has no census); the
  docs update states `census` is `null` on a startup failure so consumers key on
  `failed_passes`/`notify`, not on `census` being present.
- **Trade-off: not fsync-durable like `write_ledger`.** → Acceptable: the summary
  is a re-derivable health signal, not the irreplaceable ledger. Atomicity (no
  torn write) is sufficient; full durability is a non-goal.

## Migration Plan

Pure code + docs change to a read-only maintenance script; no data model, no
schema, no deployment dependency. Rollback is reverting the commit. Verified with
`bash scripts/gate.sh` (≥80% diff coverage vs `origin/dev`).

## Open Questions

None — the issue is a self-contained, operator-resolved work order.
