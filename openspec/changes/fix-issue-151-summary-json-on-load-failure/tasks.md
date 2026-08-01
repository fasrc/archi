## 1. Failing tests first (TDD — red)

- [x] 1.1 In the goldenset maintenance tests, add a test: `report --summary-json <path>` against a malformed/unreadable bank, WITH a pre-existing healthy summary file at `<path>` → the file is overwritten with the failure state (`failed_passes` names the bank error, `census` is `null`, `notify` is `true`) AND `main`/`run_report` exits non-zero. Assert the previous run's success values are gone.
- [x] 1.2 Add a test: `report --summary-json <path>` against a missing bank file (path does not exist) → same failure summary is written AND exit is non-zero (load failure, not a pass failure).
- [x] 1.3 Add a regression test: a healthy bank still writes the normal summary (census present, `failed_passes == []`, `notify` computed from `_NOTIFY_ON` as today) and exit code is unchanged.
- [x] 1.4 Add an atomicity test: simulate the summary write failing before the `os.replace` commit (e.g. patch to raise) → a pre-existing summary file is left intact (not truncated) and no temp file litter remains; the run surfaces `OperationalError("cannot write ...")`.
- [x] 1.5 Run the new tests and confirm they FAIL against the current code (red).

## 2. Implementation (green)

- [x] 2.1 Add a `_write_summary(args, summary)` helper in `scripts/benchmarking/goldenset_maintenance.py` that writes `args.summary_json` atomically (same-directory temp file + `os.replace`, remove temp on any failure), preserving the current `OperationalError("cannot write {path}: {exc}")` on `OSError`. Model it on `write_ledger`'s commit step.
- [x] 2.2 In `run_report`, initialize the `summary` dict BEFORE `load_bank`, seeded as failed-startup: `census = None`, `failed_passes = []`, `notify = True`.
- [x] 2.3 Wrap the `load_bank`/`bank_status_counts` call in `try/except OperationalError`. On success: set `summary["census"]` and clear `summary["notify"]` (recomputed after passes as today). On failure: set `failed_passes = ["bank: <error>"]`, `census = None`, `notify = True`, call `_write_summary`, then re-raise so `main` prints to stderr and returns 1.
- [x] 2.4 Replace the existing `:1061-1069` plain-`open` write block with a call to `_write_summary` so the normal path is atomic too.
- [x] 2.5 Run the tests from section 1 and confirm they PASS (green).

## 3. Docs & comment reconciliation

- [x] 3.1 Update the code comment in `run_report` (currently at ~`:1062`) so it states the summary is written on every terminating path, including a bank-load failure.
- [x] 3.2 Update the "summary JSON" subsection of `docs/docs/benchmarking.md`: the summary is always current (never stale after a failed startup), a startup failure yields `census = null` + `failed_passes` naming the bank error + `notify = true`, and the non-zero exit code remains the authoritative signal.

## 4. Gate

- [x] 4.1 Run `bash scripts/gate.sh` (format → lint → test, ≥80% diff coverage vs `origin/dev`) and confirm it exits 0.

## 5. Review round 2 — Codex on PR #162 (4 findings)

Making the summary write reachable on the startup-failure path put a *write* on
a code path that previously only read. Three of the four findings are the
consequences of that; the fourth is the doc overclaim it invited.

- [x] 5.1 **P1 — `--summary-json` may alias the bank.** Red test: `report` with
  `--summary-json` = `--bank` against a malformed bank replaces the bank with
  the failure summary. Fix: `reject_aliased_outputs` in `main`, refusing any
  output path (`--summary-json`, `--ledger`) that names another declared path,
  by device+inode and by resolved path. Refused before dispatch, so nothing is
  read or written.
- [x] 5.2 **Class sweep for 5.1.** The other output is `--ledger`: with
  `--ledger` = `--corpus-json`, `--undecline` reads the corpus dump as a decline
  list and writes the remainder back over it (verified: exit 0, dump one row
  short). Covered by the same guard. `--ledger` = `--bank` bounces off
  decline-entry validation today, but only by luck; it is covered too.
- [x] 5.3 **P2 — non-string `anchor_type`.** Red tests: a numeric
  `anchor_type` mixed with text kills the run at
  `sorted(census["anchor_type"].items())` (a `TypeError` no handler catches, so
  no summary is written); an all-numeric bank does not crash at all and reports
  a stringified bucket that is nowhere in the bank. Fix: validate the field's
  type in `bank_census` up front and drop the `except TypeError` backstop, which
  the validation strictly subsumes.
- [x] 5.4 **P2 — replacement loses the target's group/ACL.** Red tests: group
  ownership and a POSIX ACL, on both the summary and the ledger.
  `_preserve_mode` became `_preserve_access` — chown, then chmod, then xattrs
  (`system.posix_acl_access` included, copied with stdlib `os.*xattr`), all best
  effort. Both call sites share it.
- [x] 5.5 **P2 — docs overclaimed "always current".** Confirmed: `_write_summary`
  leaves the prior file intact when the write fails, so a file-only monitor can
  still read a stale success. New "The file is not self-certifying" section
  names the two states that read as success from the file alone, and requires
  exit code + freshness. Also fixed the swallowed diagnosis: a write failure
  raised from inside the bank handler used to replace the bank error as the one
  `main` printed — both now go to stderr, and the bank error propagates.
- [x] 5.6 Docs for the new user-facing refusals (aliased paths, non-text
  `anchor_type`) and the preserved-permission guarantee, in
  `docs/docs/benchmarking.md`.
- [x] 5.7 Mutation-check every fix: revert it, confirm the owning test fails,
  restore. 8 mutations, each caught only by its own tests.
- [x] 5.8 Re-run the gate.
- [x] 5.9 Line coverage over the script (not in the gate's `--cov=src` scope)
  showed one untested branch added in 5.3: the `isinstance(record, dict)` guard
  in `bank_census`. `normalize_bank` does pass a non-object row through, and the
  census has always counted it under `unassigned`, so without the guard the fix
  would have turned a censusable bank into a hard failure. Test added; mutation
  check — drop the guard → `AttributeError: 'str' object has no attribute
  'get'`, that test alone.
