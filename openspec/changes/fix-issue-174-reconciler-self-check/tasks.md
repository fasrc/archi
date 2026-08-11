## 1. Red: pin the bug

- [x] 1.1 Extend `mk_node()` in `scripts/ci/test_pr_readiness_labels.sh` to accept a status-check rollup: a contexts JSON array plus an optional `totalCount` override for simulating truncation, defaulting to an empty rollup so all 22 existing cases keep passing unchanged.
- [x] 1.2 Add a `mk_checks()` helper that builds rollup context nodes for both union members — `CheckRun` (`__typename`, `name`, `status`, `conclusion`) and `StatusContext` (`__typename`, `context`, `state`).
- [x] 1.3 Add the failing case: a non-draft, `MERGEABLE` PR with zero live threads whose only non-successful check is the `reconcile` job in progress. Assert it IS granted `ready-to-merge`.
- [x] 1.4 Run the suite and watch 1.3 fail against today's predicate (it sees `UNSTABLE`). Record the failure output before writing any implementation.

## 2. Green: replace the check-state clause

- [x] 2.1 Define the excluded job name once in `scripts/ci/pr_readiness_labels.sh`, near the other configuration constants, with a comment tying it to the `jobs.<id>` key in `.github/workflows/pr-readiness-labels.yml` (which this change must NOT modify).
- [x] 2.2 Add the status-check rollup to `QUERY`: `commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){ totalCount nodes{ __typename ... on CheckRun{name status conclusion} ... on StatusContext{context state} } }}}}}`.
- [x] 2.3 Extend `FILTER` with three TSV columns — blocking-check count, rollup `totalCount`, fetched-context count — passing the excluded job name in via `--arg`. Treat a null `statusCheckRollup` as `0`/`0`/`0`.
- [x] 2.4 Implement the blocking rule in the `jq` filter: skip `CheckRun`s whose name equals the excluded job; count a `CheckRun` as passing only when its conclusion is `SUCCESS`, `NEUTRAL` or `SKIPPED`; count a `StatusContext` as passing only when its state is `SUCCESS`; everything else, including a null conclusion, is blocking.
- [x] 2.5 Widen the `while IFS=$'\t' read -r ...` destructuring to bind the three new columns.
- [x] 2.6 Replace the `[ "$state" != "CLEAN" ]` readiness clause with a blocking-count test, and add a preceding fail-closed clause for rollup truncation (`totalCount` greater than fetched). Set a precise `why` string for each.
- [x] 2.7 Keep `mergeStateStatus` in the query and in the `UNKNOWN` retry/unverifiable handling — only the readiness clause stops consulting it. Leave the `mergeable == CONFLICTING` conflict derivation untouched.
- [x] 2.8 Run the suite; 1.3 now passes and all 22 pre-existing cases still pass.

## 3. Cover the rest of the contract

- [x] 3.1 Negative case: a PR **already holding** `ready-to-merge` with a non-`reconcile` check concluding `FAILURE` → assert the chip is removed.
- [x] 3.2 Case: a non-`reconcile` check pending/in-progress (null conclusion) → not granted.
- [ ] 3.3 Case: all non-`reconcile` checks `SUCCESS`/`NEUTRAL`/`SKIPPED` → granted.
- [ ] 3.4 Case: empty/null rollup on an otherwise-ready PR → granted (check state does not withhold).
- [ ] 3.5 Case: rollup `totalCount` exceeds fetched contexts → fail closed; not granted, and a held chip is revoked.
- [ ] 3.6 Case: `mergeable == CONFLICTING` → `conflicts` applied, `ready-to-merge` never applied — including the conflicted-draft variant.
- [ ] 3.7 Case: draft PR with all checks green → still not ready. Case: one unresolved review thread with all checks green → still not ready.
- [ ] 3.8 Case: a failing `StatusContext` (legacy commit status) blocks, proving the non-`CheckRun` union member is handled.

## 4. Mutation-verify the own-job match

- [ ] 4.1 Temporarily change the excluded job name constant to a different string, run the suite, and confirm at least one case fails. Paste the failing output into the PR body, then restore the constant.
- [ ] 4.2 Confirm the restored suite is green, so the mutation was demonstrated rather than merely asserted.

## 5. Docs, gate, PR

- [ ] 5.1 Update the "PR Readiness Labels" section of `docs/docs/developer_guide.md` (line ~304) to describe the new predicate: individual checks, the reconciler's own job excluded by name, permitted conclusions, and the fail-closed truncation rule.
- [ ] 5.2 Run `bash scripts/gate.sh` and confirm it exits 0 with ≥80% diff coverage.
- [ ] 5.3 Run `bash scripts/ci/pr_readiness_labels.sh --dry-run --repo fasrc/archi` and capture the output for the PR body. It changes nothing.
- [ ] 5.4 Open the PR against `dev` referencing this issue and including the `--dry-run` output and the 4.1 mutation evidence. Confirm `.github/workflows/**` is absent from the diff (`git diff --name-only origin/dev...HEAD`).
