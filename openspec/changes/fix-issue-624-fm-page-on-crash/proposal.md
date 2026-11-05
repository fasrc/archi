## Why

Issue #624. The feature-matrix wrappers page the operator (#504) only on refusals that the
wrappers name. Both wrappers run under `set -euo pipefail`, so any other failure after the
long work starts exits at once and sends no page. Known silent exits (line numbers on
this branch after the merge of `origin/dev` `1ca97e24`):

- `qa_arm.sh:92` (sweep) and `qa_arm.sh:173` (arm): `AFTER="$(fm_fingerprint "$STACK")"`
  aborts when the data-manager is down after the QA run, before the paged drift check.
- `qa_arm.sh:88-89` (sweep `qa run` and `qa score`) and `qa_arm.sh:161-167` (arm
  `qa`): a crash after a run of an hour or more.
- `qa_arm.sh:95`, `qa_arm.sh:176` and `archive_run.sh:301`: `fm_ledger_append`
  (`lib.sh:412`) fails, for example on a full disk.
- `qa_arm.sh:94` and `qa_arm.sh:175`: `fm_require_scored` (#434) refuses a run that scored
  nothing with `fm_die`, after the QA run. #434 design D6 leaves this page to #624.
- A SIGHUP, SIGINT or SIGTERM to the wrapper during the QA run (a dropped ssh session, a
  systemd stop): bash runs the `EXIT` trap with status 0, so an `EXIT` trap alone sends no
  page.

**Scope change (operator, 2026-10-20).** The issue first also asked for the corpus pin to
be written after the ledger row. #661 (`4e361f97`) did that in `archive_run.sh` and in
`sweep_tools.py`, and #696 (`c2e885ca`) hardened it. This change is paging only.

**Resume.** Tasks 1.1 and 1.2 are on `fix/issue-624-fm-page-on-crash` (`1be4177e`,
`8b6ae518`): the trap in `lib.sh` and the two `qa_arm.sh` arm points. The branch merged
`origin/dev` `1ca97e24` (60 commits); the merge renumbered the branch's self-test checks
78-81 to 98-101 and renamed `archi eval qa` to `archi qa` in them (#564).

## What Changes

- `lib.sh` has `fm_arm_page_on_failure <subject-context>` (done in task 1.1). It installs
  an `EXIT` trap that pages once when the wrapper exits non-zero and no page was sent in
  this run, and HUP, INT and TERM traps that page the same way.
- `fm_page` records that a page was sent (`FM_PAGED=1`, done in task 1.1), so the trap
  never sends a second page after `fm_die_paged` or after the validator refusal page in
  `archive_run.sh`.
- `qa_arm.sh` arms the trap directly before the QA run, in arm mode and in sweep mode
  (done in tasks 1.1 and 1.2). `archive_run.sh` arms the trap directly after the "no
  artifact" precondition, in arm mode and in sweep mode (new).
- The page body says why the run stopped (new): `fm_die` records its message, and the
  body carries it; otherwise the body names the failed command with its `file:line`, so a
  failure inside `lib.sh` no longer shows a `lib.sh` line as a wrapper line.
- A signal ends the wrapper as that signal (new). Task 1.1's traps ended with `exit 143`,
  so a calling chain saw a normal exit and could go on after Ctrl-C. The traps now page
  and then send the same signal to the wrapper itself.
- New self-test checks 102-107: a failed ledger write in `qa_arm.sh` and in
  `archive_run.sh`, the "scored nothing" refusal, SIGTERM and SIGHUP to the wrapper's
  process group, each with exactly one page, and "no artifact" with no page.
- The runbook in `docs/docs/proposals/feature-matrix-campaign-2026.md` names the new paged
  exits, and its two "does not page" sentences about the "scored nothing" refusal change.

## Capabilities

### New Capabilities

### Modified Capabilities

- `feature-matrix-operator-paging`: adds a requirement to page on any unpaged failure
  after long work. The #504 change (`fix-issue-504-fm-page-mail`) and the #629 change
  (`fix-issue-629-fm-page-watchdog`) are not archived yet, so `openspec/specs/` has no
  `feature-matrix-operator-paging` spec and this change uses ADDED requirements only.

## Impact

- Code: `scripts/benchmarking/feature_matrix/{lib.sh,qa_arm.sh,archive_run.sh}`.
- Tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (run by
  `scripts/gate.sh`).
- Docs: `docs/docs/proposals/feature-matrix-campaign-2026.md` (the "**Paging (#504).**"
  paragraph and the `qa_arm.sh` step that reads `summary.json`).
- No change to `deploy/`, to precondition refusals, to exit codes, or to the pin writes.
  A wrapper killed by a signal dies of that signal, as it did before task 1.1.
- Operator-visible: one more mail for a void QA run ("scored nothing"), and one mail when
  a QA run or an archive is stopped by SIGHUP, SIGINT or SIGTERM.
