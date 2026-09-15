## Why

The readiness reconciler triggers on `pull_request` events, which registers the
`reconcile` job as a check on the PR it is judging. While that job runs, the PR's
rolled-up `mergeStateStatus` is `UNSTABLE` *because of the reconciler itself*. The
predicate reads its own incompleteness as "not ready" and revokes `ready-to-merge`
12 seconds after granting it (measured on PR #171 on 2026-07-31). The chip therefore
flaps, and in practice can only settle via the hourly sweep — where no self-check is
in flight — rather than via the fast event-triggered runs that #170 wanted.

## What Changes

- Replace the readiness check-state clause in `scripts/ci/pr_readiness_labels.sh`.
  Today it requires `mergeStateStatus == CLEAN`; it will instead evaluate the
  individual checks from the GraphQL status-check rollup, ignoring the reconciler's
  own `reconcile` job by name.
- Every non-excluded check must have a passing conclusion (`SUCCESS`, or the
  permitted `NEUTRAL`/`SKIPPED`) for `ready-to-merge` to be granted.
- Keep conflict detection sourced from `mergeable == CONFLICTING`, unchanged and
  independent of check state.
- Preserve the draft, live-review-thread, and thread-truncation clauses exactly as
  they behave today.
- Fail closed when the check-rollup connection truncates, matching the existing
  precedent set by the label and review-thread truncation guards.
- Define the excluded job name in exactly one place, tied by comment to the
  `jobs.<id>` key in the workflow, so a rename is caught by a failing test.
- Extend the hermetic suite in `scripts/ci/test_pr_readiness_labels.sh` and update
  the "PR Readiness Labels" section of `docs/docs/developer_guide.md`.

**Not breaking**: the label contract, CLI surface, and exit codes are unchanged.

## Capabilities

### New Capabilities

- `pr-readiness-labels`: how the reconciler decides whether an open PR carries the
  `ready-to-merge` and `conflicts` chips — the check-state predicate, the exclusion
  of the reconciler's own job, and the fail-closed rules on truncated or
  uncomputed data.

### Modified Capabilities

<!-- None. No existing spec in openspec/specs/ covers PR readiness labelling. -->

## Impact

- `scripts/ci/pr_readiness_labels.sh` — the GraphQL query gains the status-check
  rollup; the `FILTER` gains check columns; the readiness predicate clause changes.
- `scripts/ci/test_pr_readiness_labels.sh` — new cases; already run by the gate.
- `docs/docs/developer_guide.md` — the "PR Readiness Labels" section (line ~304)
  documents the predicate and must match it.
- **Explicitly untouched**: `.github/workflows/pr-readiness-labels.yml` is a
  forbidden path. The `pull_request` triggers stay, because fast revocation is
  wanted per #170 — this change makes those triggers correct rather than removing
  them.
- Related: #169 (review-thread resolution) already landed; the predicate reads
  `isResolved` today. This change touches the check-state half only, so the two do
  not collide.
