## Why

Issues in this repository carry a working taxonomy. Across the last 60 issues: `bug` 41, `needs-human` 34, `P2` 30, `P3` 26, `ragas` 19, `enhancement` 18, plus the queue and routing chips the nightly run reads. The issue list can be filtered by kind, priority, area and status, and the nightly automation keys off it.

Pull requests carry almost nothing. Across the last 60 PRs there are exactly two labels in use: `ready-to-merge` 25, and one stray `needs-deploy`. The PR index therefore answers no question except "can this be merged right now", and it answers that only in the affirmative.

Two concrete costs, both observed.

**The index cannot say why a PR is not ready.** `ready-to-merge` is withheld for seven distinct reasons, and its absence renders identically for all of them. On 2026-09-20 all five open PRs sat unlabelled; four of them were `CLEAN` with every check green, and the real cause in every case was unresolved review threads left over from rounds that had replied to findings without resolving them. Establishing that took a GraphQL query per PR. The workflow that owns these labels already says labels "are the only readiness signal the index renders" and that a stale index "hid a single deleted symlink which had left 6 of 7 open PRs unmergeable while every check stayed green". The same blind spot is still open one field along: the index now shows *that* a PR is not ready and still not *why*.

**A PR inherits none of its issue's triage.** A PR closing a `P1` `bug` in the `ragas` area is indistinguishable in the list from a docs typo. The judgment was already made on the issue and is simply not carried across.

The fix is unusually cheap because the information is already in hand. `scripts/ci/pr_readiness_labels.sh` computes the exact reason in a `$why` variable from a single snapshot that already carries `isDraft`, `mergeable`, `mergeStateStatus`, the review-thread resolution state and the check rollup — then discards `$why` into a log line nobody reads. Turning it into a label costs no additional API call.

## What Changes

- **Five new status labels**, fully managed by the existing reconciler and derived from the snapshot it already fetches: `review-pending`, `checks-failing`, `checks-pending`, `base-behind`, `unverifiable`. With the existing `conflicts`, exactly one status label is present whenever `ready-to-merge` is withheld, and none is present when it is granted.
- **Inheritance from the closing issue.** A PR with `Closes #N` receives that issue's kind (`bug`, `enhancement`, `documentation`), priority (`P1`/`P2`/`P3`) and area (`ragas`, `upstream`) labels. Multiple closing issues take the union, and the strongest priority wins.
- **A kind fallback from the title** when a PR closes no issue, for the conventional-commit prefixes this repository already uses: `fix:` → `bug`, `feat:` → `enhancement`, `docs:` → `documentation`. Any other prefix, or none, yields no kind.
- **Two management modes, deliberately different.** Status labels are added *and* removed, because they describe live state and a stale one is worse than none. Inherited labels are grant-only: added when absent, never removed. A human who re-prioritises a PR is not fought by the next sweep, and an issue relabelled after its PR opened does not churn the PR's timeline.
- **The taxonomy gets written down.** `docs/docs/developer_guide.md` already has a "Reading the PR list" section documenting `ready-to-merge` and `conflicts`; that table is extended with the status group and a second one for the inherited group. The **issue** taxonomy is documented nowhere at all — every one of its labels is defined only in its own GitHub description and in the nightly skills, both invisible from a checkout — so it is written down in the same place, since a reader arriving at either list needs the same reference.
- **No change to the readiness predicate.** `ready-to-merge` keeps exactly the meaning and the grant/revoke asymmetry it has today. This change only reports the reason it was withheld and adds labels beside it.

## Capabilities

### New Capabilities

- `pull-request-labels`: A deterministic label taxonomy for open pull requests, reconciled from one snapshot on every event the readiness workflow already runs on. Covers a mutually-exclusive status group derived from live PR state, and a grant-only inherited group carried from the PR's closing issues.

### Modified Capabilities

None. No existing spec describes PR labelling; `scripts/ci/pr_readiness_labels.sh` is covered only by its own self-test.

## Impact

- **Code**: `scripts/ci/pr_readiness_labels.sh` — the GraphQL query gains `closingIssuesReferences` and `title`; the decision block turns `$why` into a status label and computes the inherited set; the edit builder learns grant-only labels. `scripts/ci/test_pr_readiness_labels.sh` gains cases for each new label, for mutual exclusivity, for grant-only behaviour and for idempotence over the widened set.
- **Labels**: five repository labels created, the status group. No kind, priority or area label is created: those already exist and are reused verbatim, which is the point of mirroring the issue taxonomy rather than inventing a parallel one.
- **Workflow**: `.github/workflows/pr-readiness-labels.yml` unchanged. The trigger surface already covers every event that can change any input, including the hourly sweep that is the only observer of thread resolution.
- **Docs**: the developer guide's existing "Reading the PR list" section is extended with the status and inherited groups, and gains a table for the issue taxonomy, which is documented nowhere today.
- **Blast radius**: the reconciler writes to every open PR on every sweep. The idempotence guarantee is what keeps that from spamming timelines, so the widened label set must preserve it; a test asserts zero write calls when the desired set already matches.
- **Not in scope**: judgment labels on PRs (`auto-ok`, `ai-wip`, `parked`, `needs-human`), which describe queueable work and cannot be derived deterministically; size labels, which the issue taxonomy does not have and so are not part of mirroring it; any change to how issues are labelled; and teaching the nightly triager to read PR labels.
