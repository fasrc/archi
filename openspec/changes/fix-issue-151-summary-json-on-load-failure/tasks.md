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

## 6. Review round 3 — Codex on PR #162 (1 finding)

- [x] 6.1 **P2 — a symlinked `--summary-json` is replaced, not followed.**
  Confirmed and reproduced: `open(link, "w")` leaves the link a link and updates
  the referent; `mkstemp` + `os.replace` turns the link into a regular file and
  leaves the referent stale. Making the summary write atomic in 2.1 regressed
  this. Fixed by `_resolved_target` (`os.path.realpath`), applied before the
  temp directory is chosen.
- [x] 6.2 **Class sweep.** The identical pattern is at the ledger writer, where
  it PRE-dates this PR (`write_ledger` was already temp + replace on `dev`).
  Same fix, same helper. Also swept the ledger's **lock sidecar**, which was
  named after the path as typed: resolving the write but not the lock lets two
  spellings of one ledger take two locks and lose a decline.
- [x] 6.3 Ordering matters, not just resolution: resolving at the commit while
  creating the temp beside the *link* passes every same-filesystem test and then
  dies on `EXDEV` when the link crosses a mount. Pinned by a cross-filesystem
  test (referent on `/dev/shm`); mutation-checked with exactly that half-fix.
- [x] 6.4 Following the link makes the round-2 aliasing guard load-bearing — a
  `--summary-json` symlinked to the bank would now write THROUGH to the bank.
  Regression test added; it passed unchanged, so the guard already covered it.
- [x] 6.5 Docs: "A symlinked output path is followed, not replaced".
- [x] 6.6 Mutation-check all four (summary resolve, ledger resolve, lock
  resolve, naive half-fix) and re-run the gate.

## 7. Review round 4 — Codex on PR #162 (2 findings)

- [x] 7.1 **P1 — the ledger transaction resolved its path three times.** Round 3
  named the lock sidecar after the *resolved* ledger, which closed the
  two-spellings-two-locks hole; the read and the write still resolved on their
  own. Confirmed: retargeting the symlink after the lock is taken leaves the
  command holding the old referent's sidecar while it reads and replaces the new
  one, and a concurrent command on that file takes its own lock. `ledger_lock`
  now resolves once and **yields** the pinned path; `run_decline` and
  `run_undecline` read and write it.
- [x] 7.2 Scope recorded in the spec: the coverage pass's read-only load runs
  *before* the transaction opens and legitimately resolves at its own open.
  Pinning it would claim a guarantee no lock is held for, so the requirement is
  written against the read-modify-write inside the lock — a later reader will not
  re-raise it as a miss.
- [x] 7.3 **P2 — a hard-linked output was silently decoupled.** Confirmed as a
  real behaviour change from this PR's atomic write. **Partial pushback on the
  remedy:** neither option offered is available. Writing in place restores the
  shared inode by reopening the partial-write window this PR exists to close, and
  does not help a consumer holding an open fd anyway; *refusing* the write leaves
  the monitor reading the previous healthy summary forever, which is the failure
  the summary contract exists to close. The defect is the silence, so the write
  commits and the run names the decoupled path.
- [x] 7.4 Class sweep: applied at both writers via `_warn_if_multiply_linked`,
  the summary and the ledger, as in 6.2.
- [x] 7.5 Docs: "A hard-linked output path is reported, not followed", plus the
  once-per-transaction sentence on the symlink section.
- [x] 7.6 Mutation-check all four edits (lock yield, decline call site, summary
  warning, ledger warning) and re-run the gate.
- [x] 7.7 **Self-review of 7.3, before the re-review returned.** The warning is
  emitted INSIDE both writers' `try`, so a stderr that cannot take it fails the
  write it was describing — `OSError` (`2>&-` → `EBADF`) is caught by the
  writer, deletes the staged temp and reports "cannot write" for a write that
  was ready to commit; `ValueError` (stream closed under the process) is not
  caught by that handler at all and escapes, leaving the temp file as litter and
  leaking `write_ledger`'s directory handle. Both now contained in the helper.
  Mutation-checked: narrowing the guard to `OSError` alone fails the closed-
  stream test, so the second half is load-bearing.

## 8. Review round 5 — Codex on PR #162 (3 findings)

- [x] 8.1 **P2 — `os.chown` is unguarded on non-POSIX.** Confirmed:
  `AttributeError` is not `OSError`, so it escaped the writer before the unlink.
  `_copy_xattrs` already drew this boundary with `hasattr(os, "listxattr")` and I
  did not carry it to `chown`. Extracted `_copy_ownership` with the same guard.
- [x] 8.2 **P2 — the cleanup handler's own `close()` can raise.** Confirmed: on
  `ENOSPC` the flush inside `close()` raises the same error again, destroying the
  report of the first and skipping the unlink.
- [x] 8.3 **Class fix, not three patches.** 8.1, 8.2 and 7.7 are one defect: the
  cleanup lived in an `except OSError` and assumed every failure in the block was
  one. Cleanup now runs in a `finally` keyed on a `committed` flag, through
  `_close_quietly` / `_discard_quietly`, so any exception type leaves no temp
  file and cleanup cannot replace the error that caused it. 7.7's guard is kept
  and is not redundant — the `finally` prevents the *litter*, the guard prevents
  a warning from *failing the write at all*.
- [x] 8.4 **P2 — `Path.resolve()` raises `RuntimeError` on a symlink loop.**
  Confirmed on the interpreter in use (3.11.15): `Path.resolve()` raises where
  `os.path.realpath` returns the path. `main` catches only `OperationalError`, so
  a self-referential `--bank` ended the run on a traceback *before* `run_report`
  and the summary was never written — this capability's own failure, from a
  direction the change had not looked at. The guard now resolves with
  `_resolved_target`, which also makes it agree with the writers instead of using
  a second resolver on the same paths.
- [x] 8.5 Negative control: an output spelled through a symlinked directory that
  names an input is still refused, so making resolution total did not make the
  guard blind.
- [x] 8.6 Out of scope, filed separately: `src/utils/goldenset_maintenance.py:305`
  and `:307` resolve a persisted document path against the data root with
  `Path.resolve()` and carry the same `RuntimeError` exposure. Pre-dates this PR,
  different module, and it is a path-containment security check — not something
  to alter inside an unrelated change.
- [x] 8.7 Mutation-check all three (chown guard, `finally` cleanup, resolver) and
  re-run the gate.

## 9. Review round 6 — Codex on PR #162 (6 findings, two batches)

All six confirmed. Batch 1 (4 findings, 19:25) was worked by the nightly loop in
`582211e3` while this session was idle; batch 2 (2 findings, 08:33) arrived after
that push and is worked here in `HEAD`. Both are recorded together because the
findings are one story: `os.replace` replaces a **name**, and each shape that
name can hold was being handled at the write, one at a time, which is what kept
producing the next one.

### Batch 1 — `582211e3` (nightly loop)

- [x] 9.1 **P1 — the alias guard and the writers resolved independently.** Same
  class as 7.1, one layer up: `report` spends its network passes between them, so
  retargeting a symlinked output mid-run walks past the refusal and lets
  `os.replace` land on a file it never examined, the bank included. Each output's
  resolution is pinned at the guard and handed to the writers via
  `pinned_output`.
- [x] 9.2 **P2 — a symlink loop at an OUTPUT is destroyed.** A regression from
  8.4: the non-strict resolver that keeps a looped *input* from ending the run on
  a traceback returns the loop unresolved, and `os.replace` then swaps the link
  itself for a regular file. Refused; a resolution that is still a symlink is the
  signal that resolution gave up. A dangling link is not that, and is still
  written through.
- [x] 9.3 **P2 — `os.close(dir_fd)` was left bare** beside the `_close_quietly`
  and `_discard_quietly` from 8.3. `_close_fd_quietly`, applied on **both** sides
  of the commit — after it, a raw `OSError` would replace `LedgerNotDurable` or
  manufacture a failure out of a run that succeeded.
- [x] 9.4 **P2 — atomic writes need a writable parent directory.** Confirmed and
  inherent: staging a sibling needs create/rename on the directory where
  `open(path, "w")` needed write on the file. Not fixable, so documented as a
  deployment prerequisite, which is what the finding asked for.

### Batch 2 — this commit

- [x] 9.5 **P2 — a FIFO output is unlinked and its consumer disconnected.**
  Refused, unlike the hard-linked target in 7.3: there the choice was between a
  stale second name and no health signal at all, whereas here nothing has been
  written and the endpoint still exists. Written as "not a regular file", so a
  socket or device node is the same answer.
- [x] 9.6 **The refusal had to move to the guard, not the write.** 9.2 validated
  at the writers; a FIFO `--ledger` is *read* first, and reading a pipe with no
  writer never returns. Left there, the tool hangs instead of refusing —
  demonstrated by the mutation: with the check removed the suite does not fail,
  it stops responding. Output validation now runs inside
  `reject_aliased_outputs`, before the first read.
- [x] 9.7 **P2 — an unexpected exception skipped the summary write.** Third
  arrival through the same gap (`TypeError` round 2, `RuntimeError` 8.4,
  `AttributeError` here): the contract said "every terminating path", the
  implementation was a call at each known exit. The write is now one `finally`
  around the whole run, reached for any exception type.
- [x] 9.8 **Regression caught by an existing test while doing 9.7.** The first
  `finally` swallowed the write error unconditionally, so a failed summary write
  on an otherwise-clean run started reporting success — the exact stale-file
  failure this capability exists to close.
  `test_write_interrupted_before_commit_leaves_prior_file_intact` failed and I
  kept it: the write error propagates unless a worse one already is.
- [x] 9.9 Mutation-check the three (non-regular refusal, the total `finally`, the
  propagate-unless-already-failing rule) and re-run the gate.

### Note on concurrent work

- [x] 9.10 This session had independently fixed batch 1 before discovering
  `582211e3` on the remote. That work was **discarded in favour of the loop's**,
  which was better on two counts: `_close_fd_quietly` on both sides of the
  commit, which this session missed, and pinning through an explicit
  `pinned_output` accessor rather than rewriting `args` in place, which would
  have changed operator-facing paths in the run's own output. Only batch 2 and
  the OpenSpec artifacts are carried forward.
