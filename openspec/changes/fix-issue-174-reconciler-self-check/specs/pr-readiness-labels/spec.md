## ADDED Requirements

### Requirement: Readiness ignores the reconciler's own check

The readiness predicate SHALL exclude the reconciler's own `reconcile` job from the check state it evaluates.
Because the workflow triggers on `pull_request` events, the reconciler registers as a check on the very PR it
is judging; counting that check makes every event-triggered run read its own incompleteness as "not ready".
The job is identified by name, and that name is defined in exactly one place in the script, carrying a comment
tying it to the `jobs.<id>` key in `.github/workflows/pr-readiness-labels.yml`.

#### Scenario: Only the reconciler's own check is outstanding
- **WHEN** a non-draft PR has no live review threads, is `MERGEABLE`, and its only non-successful check is the `reconcile` job in a pending or in-progress state
- **THEN** the PR is granted `ready-to-merge`

#### Scenario: The chip does not flap on an event-triggered run
- **WHEN** the reconciler runs from a `pull_request` event on a PR that already holds `ready-to-merge` and nothing but its own job is outstanding
- **THEN** the chip is left in place and is not revoked

#### Scenario: A rename of the excluded job breaks a test
- **WHEN** the single definition of the excluded job name is changed to any other string
- **THEN** at least one case in the hermetic suite fails

### Requirement: Every non-excluded check must conclude successfully

The readiness predicate SHALL require every check other than the excluded `reconcile` job to have concluded successfully.
`SUCCESS` counts as passing, as do the permitted non-failing conclusions `NEUTRAL` and `SKIPPED`. Any other
conclusion, and any check still pending or in progress, withholds `ready-to-merge`. This replaces the previous
reliance on the rolled-up `mergeStateStatus == CLEAN`, which folded the reconciler's own state into the verdict.

#### Scenario: A genuinely failing check withholds the chip
- **WHEN** a PR currently holding `ready-to-merge` has a non-`reconcile` check whose conclusion is `FAILURE`
- **THEN** `ready-to-merge` is removed from that PR

#### Scenario: Another check still running withholds the chip
- **WHEN** a PR has a non-`reconcile` check that is pending or in progress
- **THEN** `ready-to-merge` is not granted

#### Scenario: Neutral and skipped checks do not withhold the chip
- **WHEN** every non-`reconcile` check has concluded `SUCCESS`, `NEUTRAL` or `SKIPPED`
- **THEN** the check-state clause is satisfied and does not withhold `ready-to-merge`

#### Scenario: A PR with no checks at all is not blocked by check state
- **WHEN** a non-draft, mergeable PR with no live review threads has an empty check rollup
- **THEN** the check-state clause does not withhold `ready-to-merge`

### Requirement: A truncated check rollup fails closed

The readiness predicate SHALL withhold `ready-to-merge` when the status-check rollup connection is truncated.
When the rollup's `totalCount` exceeds the number of contexts actually fetched, a failing check may be sitting
in the unfetched tail, so the verdict is unverifiable. This matches the existing fail-closed handling of a
truncated review-thread connection and the authoritative re-read used for a truncated label connection.

#### Scenario: Rollup exceeds the fetched page
- **WHEN** a PR's status-check rollup reports a `totalCount` greater than the number of contexts returned
- **THEN** `ready-to-merge` is not granted, and a held chip is revoked, with the truncation given as the reason

#### Scenario: A full rollup is evaluated normally
- **WHEN** the rollup's `totalCount` equals the number of contexts returned
- **THEN** the checks are evaluated on their conclusions with no truncation penalty

### Requirement: An empty rollup is trusted only when the merge state agrees

The readiness predicate SHALL withhold `ready-to-merge` when no checks are on record and `mergeStateStatus` is `BLOCKED`.
An empty rollup has two meanings the rollup alone cannot separate: a PR that genuinely runs no checks, and a PR
in the window between `opened`/`synchronize` and its first check run registering. The reconciler fires on those
very events, so treating "no contexts" as "no block" unconditionally advertises readiness before CI has produced
a result. `BLOCKED` is the signal that the base is still expecting a required check it has not seen. The guard is
confined to an empty rollup, so it cannot re-block a PR on the reconciler's own in-progress check: that check run
sits on the head commit, making the rollup non-empty.

#### Scenario: No checks on record while the base still expects one
- **WHEN** a PR reports `mergeStateStatus == BLOCKED` and its head commit carries no status-check contexts
- **THEN** `ready-to-merge` is not granted, and a held chip is revoked

#### Scenario: No checks on record and nothing outstanding
- **WHEN** a PR reports `mergeStateStatus == CLEAN` and its head commit carries no status-check contexts
- **THEN** `ready-to-merge` is granted, because no check is expected

#### Scenario: The reconciler's own in-progress check does not trip the guard
- **WHEN** a PR reports `mergeStateStatus == BLOCKED` and the only context on its head commit is the reconciler's own in-progress check
- **THEN** `ready-to-merge` is granted, because the rollup is not empty and the reconciler's own check is excluded

### Requirement: A PR behind its base is not ready

The readiness predicate SHALL withhold `ready-to-merge` when `mergeStateStatus` is `BEHIND`.
The status-check rollup hangs off the PR's head commit and is base-agnostic: it records that the checks
passed, never which base they were merged against. Retargeting a PR arrives as an `edited` event, which the
reconciler observes but neither check producer does, so the head commit keeps the green rollup it earned
against the old base. `BEHIND` is the one base-relative signal the API offers — the head ref is out of date,
so the checks on record cannot have tested the merge result.

#### Scenario: Behind the base with green checks
- **WHEN** a PR reports `mergeStateStatus == BEHIND` and every check on its head commit has concluded `SUCCESS`
- **THEN** `ready-to-merge` is not granted, and a held chip is revoked

#### Scenario: Behind is not a conflict
- **WHEN** a PR reports `mergeStateStatus == BEHIND` and `mergeable == MERGEABLE`
- **THEN** the `conflicts` chip is not applied

### Requirement: Conflict labelling stays sourced from mergeable

The system SHALL continue to derive the `conflicts` chip from `mergeable == CONFLICTING` alone, independent of check state.
`mergeStateStatus` is a priority field that reports `DRAFT` on a draft PR, masking `DIRTY`, so a conflicted draft
would otherwise receive no chip. Replacing the check-state clause SHALL NOT alter this derivation, and a
conflicted PR SHALL never hold `ready-to-merge`.

#### Scenario: Conflicted PR gets the conflicts chip
- **WHEN** a PR reports `mergeable == CONFLICTING`
- **THEN** it is given `conflicts` and is not given `ready-to-merge`

#### Scenario: Conflicted draft still gets the conflicts chip
- **WHEN** a draft PR reports `mergeable == CONFLICTING`
- **THEN** it is given `conflicts`

### Requirement: The remaining readiness clauses are unchanged

The system SHALL preserve the draft, live-review-thread, thread-truncation, and uncomputed-mergeability clauses exactly as they behave today.
Only the check-state clause changes. A draft PR is never ready; a PR with one or more unresolved review threads
is never ready; a PR whose review-thread connection is truncated is never ready; and a PR whose `mergeable` or
mergeability is still `UNKNOWN` after the bounded retries has a held `ready-to-merge` revoked while `conflicts`
is left untouched.

#### Scenario: Draft PR is never ready
- **WHEN** a PR is a draft and all its non-`reconcile` checks have concluded `SUCCESS`
- **THEN** `ready-to-merge` is not granted

#### Scenario: Live review threads still withhold the chip
- **WHEN** a PR has one or more review threads with `isResolved == false`
- **THEN** `ready-to-merge` is not granted, regardless of check state

#### Scenario: Uncomputed mergeability still revokes a held chip
- **WHEN** a PR's `mergeable` is still `UNKNOWN` after the bounded retries and it holds `ready-to-merge`
- **THEN** the chip is revoked and `conflicts` is left untouched

### Requirement: The snapshot remains a single consistent read

The system SHALL continue to fetch the check rollup in the same paginated GraphQL snapshot used for the rest of the predicate.
Check state is read for every open PR in the same query that already carries `isDraft`, `mergeable`, labels and
review threads, so every PR in a sweep is judged against one snapshot. A failed query or an unparseable response
SHALL remain a loud operational failure rather than being treated as an empty sweep.

#### Scenario: Check state travels with the existing snapshot
- **WHEN** a sweep fetches open PRs
- **THEN** each PR's check rollup arrives in the same query result as its draft, mergeable, label and review-thread data

#### Scenario: An unreadable snapshot is still a loud failure
- **WHEN** the GraphQL query fails or its response cannot be parsed
- **THEN** the script exits non-zero and strips no chips
