## Why

The feature-matrix wrappers stop at points that need a human, but they only print a marker
to stderr. On 2026-09-09 a chain idled for three hours at a manual step that nobody saw.
The operator decided on 2026-09-26 (issue #504, option A) to page by mail: cron mail is
the only operator channel this repository has (`scripts/benchmarking/goldenset_report_cron.sh`).

The two exits that need a human after long work are:

- `scripts/benchmarking/feature_matrix/archive_run.sh:66` (sweep mode) and `:96` (arm
  mode): `|| fm_die "refusing to archive $ARTIFACT (see above)"`. The checks print the
  refusal reason to stderr and exit 2.
- `scripts/benchmarking/feature_matrix/qa_arm.sh:92` (sweep mode) and `:171` (arm mode):
  `fm_die "corpus changed during the QA run ..."` after an hour-long QA run.

## What Changes

- `lib.sh`: two new knobs, `FM_PAGE_MAIL_TO` (default empty = paging off) and `FM_MAIL`
  (the mail binary, default `mail`), documented in the header comment.
- `lib.sh`: `fm_page <subject> <body>` sends one mail and never fails the caller, and
  `fm_die_paged <subject-context> <msg>` pages and then dies like `fm_die` (exit 2).
- `archive_run.sh`: both "refusing to archive" sites page, with the refusal reason that the
  check printed in the body. The reason still appears on stderr as before.
- `qa_arm.sh`: both corpus-drift exits page.
- Precondition refusals before long work do not page (the operator is at the keyboard).
- `test_feature_matrix_wrappers.sh`: a fake `mail` stub (via `FM_MAIL`) and new cases.
- `docs/docs/proposals/feature-matrix-campaign-2026.md` §5: document the two knobs.

## Capabilities

### New Capabilities
- `feature-matrix-operator-paging`: the feature-matrix wrappers mail the operator when a
  run stops after long work at a point that needs a human.

### Modified Capabilities
None.

## Impact

- `scripts/benchmarking/feature_matrix/{lib.sh,archive_run.sh,qa_arm.sh,test_feature_matrix_wrappers.sh}`.
- `docs/docs/proposals/feature-matrix-campaign-2026.md` (§5 text only).
- No Python under `src/`; `scripts/gate.sh:141` already runs the wrapper self-test, so the
  gate enforces the new cases without a change to `gate.sh`.
- Out of scope (issue #504): the `repository_dispatch` relay, `fm_page_and_wait`, and
  `page()` in the bench_out chains. The #434 scored-nothing guard is not on `dev` yet
  (#434 is open); when it lands it uses `fm_die_paged`.
