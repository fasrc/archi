## Context

`scripts/ci/pr_readiness_labels.sh` grants and revokes two managed chips on every open
PR: `ready-to-merge` and `conflicts`. Its readiness clause today is a single test at
`scripts/ci/pr_readiness_labels.sh:309`:

```sh
elif [ "$state" != "CLEAN" ]; then
  why="not CLEAN ($state)"
```

where `$state` is GitHub's rolled-up `mergeStateStatus`. That field folds draft state,
conflicts **and** aggregate check state into one verdict — which is convenient right up
to the point where the reconciler becomes one of those checks.

The workflow triggers on `pull_request`. Each trigger registers the `reconcile` job as a
check run on the PR being judged. While it runs, the rollup is `UNSTABLE`, so `$state`
is not `CLEAN`, so the predicate concludes "not ready" and revokes a chip it granted
seconds earlier. Measured on PR #171 on 2026-07-31: revoked 12 seconds after grant. The
label can only settle on the hourly sweep, where no self-check is in flight — which
defeats the fast event-triggered revocation #170 asked for.

The script is already disciplined about this class of bug. It carries `mergeable` and
`mergeStateStatus` separately because they disagree in cases that matter; it fails
closed when the review-thread connection truncates; it re-reads labels authoritatively
via `authoritative_membership()` when that connection truncates; and it checks every
command explicitly rather than trusting `set -e`. This change follows those precedents
rather than inventing new ones.

Constraints:

- `.github/workflows/pr-readiness-labels.yml` is a **forbidden path**. The fix is
  script-only. The `pull_request` triggers stay.
- `scripts/ci/test_pr_readiness_labels.sh` is a hermetic 22-case suite that stubs `gh`,
  and is run by the gate. No network in tests.
- #169 already landed: review-thread resolution reads `isResolved`. This change touches
  only the check-state half of the predicate, so the two do not collide.

## Goals / Non-Goals

**Goals:**

- A PR whose only outstanding check is the reconciler's own job is judged ready.
- A genuinely failing or still-running *other* check still withholds the chip.
- Truncation of the new connection fails closed, like every other connection here.
- The excluded job name lives in one place and a rename is caught by a failing test.
- The documented predicate in `docs/docs/developer_guide.md` matches the code.

**Non-Goals:**

- Removing the `pull_request` triggers. That needs a forbidden workflow edit, and #170
  wants fast revocation.
- Any change to `.github/workflows/pr-readiness-labels.yml`, in any form.
- #169's review-thread work — already landed.
- Changing the label names, CLI surface, exit codes, or the conflict derivation.

## Decisions

### 1. Evaluate individual checks from the status-check rollup, not `mergeStateStatus`

Add the rollup to the existing per-PR selection:

```graphql
commits(last: 1) {
  nodes { commit { statusCheckRollup { contexts(first: 100) {
    totalCount
    nodes {
      __typename
      ... on CheckRun     { name status conclusion }
      ... on StatusContext { context state }
    }
  } } } }
}
```

`contexts` is a union. `CheckRun` (GitHub Actions jobs, including ours) carries
`name`/`status`/`conclusion`; `StatusContext` (legacy commit statuses) carries
`context`/`state`. Both are handled, because a repository can accumulate either.

*Alternative rejected — keep `mergeStateStatus` and special-case `UNSTABLE`.* Treating
`UNSTABLE` as ready would ignore genuinely failing checks, which is the opposite bug and
strictly worse: the chip would then lie in the dangerous direction.

*Alternative rejected — query the Checks REST API per PR.* One request per PR breaks the
single-snapshot property the script deliberately maintains, and costs a request per PR
per sweep.

### 2. `mergeStateStatus` stays in the query, but only as the "not computed yet" signal

The field is **not** deleted. The bounded retry loop keys on either `mergeable` or
`mergeStateStatus` being `UNKNOWN` to decide that GitHub has not finished computing
mergeability, and the query itself is what prompts that computation. Only the *readiness*
clause stops consulting it. This keeps the uncomputed-mergeability behavior — revoke a
held chip, leave `conflicts` alone — exactly as it is.

### 3. A check is blocking unless it is our own job or a permitted conclusion

For each context:

- `CheckRun` whose `name` equals the excluded job name → ignored entirely.
- `CheckRun` → passing when `conclusion` is `SUCCESS`, `NEUTRAL` or `SKIPPED`. A null
  conclusion (queued/in progress) is blocking, as is any other conclusion
  (`FAILURE`, `TIMED_OUT`, `CANCELLED`, `ACTION_REQUIRED`, `STARTUP_FAILURE`).
- `StatusContext` → passing when `state` is `SUCCESS`. `PENDING`, `EXPECTED`, `ERROR`
  and `FAILURE` are blocking.

Allow-listing the passing conclusions rather than deny-listing the failing ones means a
conclusion GitHub adds later is treated as blocking — fail closed by default.

Exclusion is applied to `CheckRun.name` only. Our reconciler is an Actions job, so it can
never surface as a `StatusContext`; excluding by `context` too would widen the hole for
no benefit.

### 4. Count blocking checks in `jq`, carry the counts as TSV columns

The existing `FILTER` flattens each PR to a TSV row. It gains three columns: the number
of blocking (non-excluded, non-passing) checks, the rollup's `totalCount`, and the number
of contexts actually fetched. The shell then tests small integers, which is how every
other clause in the loop already works. A null `statusCheckRollup` — a commit with no
checks at all — yields `0`/`0`/`0` and does not withhold the chip.

The excluded job name is passed into `jq` as `--arg reconcile_job "$RECONCILE_JOB"`, with
`RECONCILE_JOB` defined once near the other configuration constants and commented as
tracking the `jobs.<id>` key in the workflow file.

### 5. Truncation fails closed

If `totalCount` exceeds the number of contexts fetched, a failing check may be in the
unfetched tail. The verdict is then unverifiable and `ready-to-merge` is withheld, with
the truncation as the printed reason — mirroring the existing review-thread clause at
`scripts/ci/pr_readiness_labels.sh:313`.

Unlike labels, there is no authoritative cheap re-read worth adding here: labels get one
because chip membership drives an *edit* decision, whereas a truncated rollup only needs
a safe verdict. 100 checks on one commit is already far outside this repository's shape.

### 6. Conflicts are untouched

`want_conflict` continues to derive from `mergeable == CONFLICTING` alone. This is
deliberately independent of check state, because `mergeStateStatus` reports `DRAFT` on a
draft PR and masks `DIRTY`.

## Risks / Trade-offs

- **A genuine failure of the reconcile job is now invisible to the predicate** → Accepted,
  and unavoidable: a job cannot both judge readiness and count itself. Its failures remain
  visible in the Actions tab and in the job's own exit status.
- **The excluded name drifts if the workflow's job is renamed**, and the workflow is a
  forbidden path this change cannot keep in sync → Mitigated by defining the name once
  with a comment naming the `jobs.<id>` key, plus a mutation-verified test: changing the
  string must make a test fail. The acceptance criterion requires this be *demonstrated*,
  not merely inspected.
- **A check name collision** — some other job also named `reconcile` would be silently
  excluded → Low risk in this repository, and the exclusion is an exact string equality on
  a specific name, not a prefix or glob.
- **`commits(last: 1)` is the PR head at snapshot time**; a push mid-sweep judges the
  older commit → Pre-existing property of the whole snapshot approach, and self-correcting
  on the next run, which the `pull_request` trigger makes prompt.
- **A required check that never gets scheduled leaves a PR permanently unready** →
  Pre-existing under `mergeStateStatus == CLEAN` too; not a regression.
- **Query cost grows** by one connection per PR → Negligible; it rides the existing
  paginated query rather than adding requests.

## Migration Plan

No data or deployment migration: a script predicate change that takes effect on the next
run. Rollback is a revert of the script commit. Before the PR, a `--dry-run` against live
`fasrc/archi` is pasted into the PR body, per the issue's acceptance criteria — it changes
nothing and shows the verdict the new predicate would reach for every open PR.

## Open Questions

None blocking. The issue body resolved the approach with the operator on 2026-08-10, and
the remaining choices above are local to the script.
