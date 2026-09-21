## 1. Repository labels

- [x] 1.1 Create the five status labels on `fasrc/archi` with descriptions that state the cause, not the remedy: `review-pending`, `checks-failing`, `checks-pending`, `base-behind`, `unverifiable`.
- [x] 1.2 Confirm no kind, priority or area label needs creating — `bug`, `enhancement`, `documentation`, `P1`, `P2`, `P3`, `ragas` and `upstream` all already exist and are reused verbatim.

## 2. Query: carry the two fields inheritance needs

- [x] 2.1 Add `title` and `closingIssuesReferences(first:10){nodes{number labels(first:50){nodes{name}}}}` to `QUERY` in `scripts/ci/pr_readiness_labels.sh`. No second query, and no per-PR call.
- [x] 2.2 Extend `FILTER` to emit the closing issues' labels and the title as additional TSV columns, escaping any tab or newline in the title so the row cannot be split by it.
- [x] 2.3 Add a test that a title containing a literal tab does not corrupt the row.

## 3. Status labels from the existing ladder

- [x] 3.1 In the decision block, set a `want_status` variable alongside `why` in each branch of the existing `if/elif` ladder, per the mapping table in `design.md`. Do not add a parallel set of conditions.
- [x] 3.2 Extend the edit builder to add `want_status` when absent and remove any other status label that is present, so at most one is ever held.
- [x] 3.3 Test: live findings earn `review-pending` (red first — assert the label is absent before the change).
- [x] 3.4 Test: a blocking check plus a live finding earns `checks-failing` only, proving the ladder's precedence is preserved.
- [x] 3.5 Test: resolving the last thread removes `review-pending` and grants `ready-to-merge` in the same sweep.
- [x] 3.6 Test: a draft carries none of the five, and a status label it already held is removed.
- [x] 3.7 Test: a conflicted PR keeps `conflicts` and gains none of the five.
- [x] 3.8 Test: each of the three unverifiable paths earns `unverifiable` — truncated threads, truncated rollup, and no checks while BLOCKED.
- [x] 3.9 Test: mutual exclusivity directly — seed a PR holding all four and assert exactly one survives.

## 4. Inherited labels

- [x] 4.1 Compute the inherited set from the closing issues' labels: union for kind and area, strongest priority via `P1` > `P2` > `P3`.
- [x] 4.2 Apply the exclusive-group rule — skip priority entirely when the PR holds any of `P1`/`P2`/`P3`, skip kind when it holds any of `bug`/`enhancement`/`documentation`.
- [x] 4.3 Add inherited labels grant-only: never build a `--remove-label` edit for this group.
- [x] 4.4 Test: a PR closing a `P3` `bug` inherits both.
- [x] 4.5 Test: a hand-set `P1` is not joined by an inherited `P3`.
- [x] 4.6 Test: an inherited label is not revoked when the issue is relabelled.
- [x] 4.7 Test: the strongest priority wins across two closing issues.
- [x] 4.8 Test: two areas from two issues both land.

## 5. Title fallback

- [x] 5.1 Map `fix`, `feat` and `docs` prefixes, with an optional `(scope)`, to `bug`, `enhancement` and `documentation`. Everything else yields nothing.
- [x] 5.2 Apply it only when the PR closes no issue and holds no kind label.
- [x] 5.3 Test: `fix(#492): ...` yields `bug`; `chore: ...` yields nothing; a closing issue outranks the title.

## 6. Ownership and idempotence

- [x] 6.1 Define the managed set as one list and derive every add and remove from it, so nothing outside it can be touched.
- [x] 6.2 Test: a PR carrying `needs-deploy` keeps it across a sweep.
- [x] 6.3 Test: a PR already matching the full desired set, status and inherited, produces zero write calls. Extend the existing idempotence case rather than adding a second one.
- [x] 6.4 Run `bash scripts/ci/test_pr_readiness_labels.sh` and record the pass count; it is wired into `scripts/gate.sh`, so the gate must be green before commit.

## 7. Documentation

- [x] 7.1 Add a label-taxonomy section to `docs/docs/developer_guide.md` covering issues and pull requests, with one table per group and a sentence on who writes each label.
- [x] 7.2 State the two management modes explicitly: status labels are reconciled both ways, inherited labels are grant-only, and everything else is human or nightly-triager territory.
- [x] 7.3 Build the docs with `mkdocs build --strict` and confirm no warning.

## 8. Rollout

- [x] 8.1 Run the reconciler with `--dry-run` against the live repository and read the decision for every open PR before anything is written.
- [x] 8.2 Check the dry-run output against the five PRs whose state is known from 2026-09-20, confirming each lands the expected status label.
- [x] 8.3 Open the PR against `dev` and let the workflow reconcile for real on its own head.

## 9. Pending is not failing (found in production on PR #517)

- [x] 9.1 Add a `$failing` count beside `$blocking` in the FILTER: contexts that FINISHED and came back bad, by CheckRun conclusion or StatusContext state. Leave `$blocking` alone — the chip's verdict must not change.
- [x] 9.2 Split the ladder branch: a finished failure says `checks-failing`, anything else non-passing says `checks-pending`. Failure outranks pending.
- [x] 9.3 Create the `checks-pending` label.
- [x] 9.4 Test: a running check earns `checks-pending` and never `checks-failing` — the case whose absence let this ship.
- [x] 9.5 Test: a completed failure, from either context type, still says `checks-failing`.
- [x] 9.6 Test: one red check outranks one still running.
- [x] 9.7 Test: `NEUTRAL` and `SKIPPED` stay passing and still earn the chip, so the split did not reclassify them.
