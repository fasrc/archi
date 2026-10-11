## Why

Issue #624. The feature-matrix wrappers page the operator (#504) only on refusals that the
wrappers name. Both wrappers run under `set -euo pipefail`, so any other failure after the
long work starts exits at once and sends no page. Known silent exits on `origin/dev`
`6156b760`:

- `qa_arm.sh:91` (sweep) and `qa_arm.sh:170` (arm): `AFTER="$(fm_fingerprint "$STACK")"`
  aborts when the data-manager is down after the QA run, before the paged drift check.
- `qa_arm.sh:87-88` (sweep `eval qa run` and `eval qa score`) and `qa_arm.sh:158-164`
  (arm `eval qa`): a crash after a run of an hour or more.
- `qa_arm.sh:93`, `qa_arm.sh:172` and `archive_run.sh:267`: `fm_ledger_append`
  (`lib.sh:359`) fails, for example on a full disk.

There is also a pin-before-ledger defect. The arm-mode artifact validator in
`archive_run.sh` writes the corpus pin (`open(pin_file, "w")` at lines 231 and 233) before
`fm_ledger_append "$ENTRY"` at line 267. The sweep path has the same order:
`sweep_tools.py:494-496` writes the corpus pin and the map pin before the ledger
`os.replace` at line 500. If the ledger write fails, a pin exists for an archive that has
no ledger row. A retry of run 1 then compares against a pin that nothing recorded.

## What Changes

- `lib.sh` gets `fm_arm_page_on_failure <subject-context>`. It installs an `EXIT` trap
  that pages once when the wrapper exits non-zero and no page was sent in this run.
  HUP, INT and TERM traps page the same way, because bash runs the `EXIT` trap with
  status 0 when a signal kills it.
- `fm_page` records that a page was sent, so the trap never sends a second page after
  `fm_die_paged` or after the validator refusal page in `archive_run.sh`.
- `qa_arm.sh` (arm and sweep mode) arms the trap directly before the QA run.
  `archive_run.sh` (arm and sweep mode) arms the trap directly after the "no artifact"
  precondition.
- `archive_run.sh` arm mode writes the corpus pin only after `fm_ledger_append` succeeds.
  `sweep_tools.py archive` writes both pins only after the ledger `os.replace` succeeds.
- The runbook paragraph "**Paging (#504).**" in
  `docs/docs/proposals/feature-matrix-campaign-2026.md` §5 names the new paged exits.

## Capabilities

### New Capabilities

### Modified Capabilities

- `feature-matrix-operator-paging`: adds a requirement to page on any unpaged failure
  after long work, and a requirement that no pin is written without its ledger row. The
  #504 change (`fix-issue-504-fm-page-mail`) is not archived yet, so this change uses
  ADDED requirements only.

## Impact

- Code: `scripts/benchmarking/feature_matrix/{lib.sh,qa_arm.sh,archive_run.sh,sweep_tools.py}`.
- Tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` (run by
  `scripts/gate.sh`) and `tests/unit/test_sweep_tools.py`.
- Docs: `docs/docs/proposals/feature-matrix-campaign-2026.md` §5.
- No change to `deploy/`, to precondition refusals, or to exit codes.
