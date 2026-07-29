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
