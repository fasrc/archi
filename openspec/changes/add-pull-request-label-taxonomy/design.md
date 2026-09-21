# Design — a label taxonomy for pull requests

## The constraint that shapes everything

`scripts/ci/pr_readiness_labels.sh` runs a single GraphQL query per page of open PRs and judges every PR in a sweep against one snapshot. That property is load-bearing: it is what makes a sweep internally consistent and what keeps the hourly reconciliation cheap enough to run on every event.

So the rule for this change is that **no new API call may be added to the per-PR path**. Everything in the status group already sits in the existing snapshot. The inherited group needs two more fields on the same query, `title` and `closingIssuesReferences`, which cost nothing extra because they ride the query that already runs.

## Decision 1 — the status label is `$why`, not a new computation

The reconciler already decides *why* it withholds the chip. The chain at the decision block produces a `$why` string through a strict `if/elif` ladder, and then uses it only in a log line.

The status label is derived from that same ladder rather than from a parallel set of conditions. Re-deriving would create two sources of truth that could disagree, and a status label that contradicts the chip beside it is worse than no status label.

Mapping, in the ladder's existing precedence order:

| Ladder branch | Status label |
|---|---|
| `isDraft` | none — GitHub renders Draft natively in the index |
| `mergeable == CONFLICTING` | none — the existing `conflicts` chip already says this |
| `mergeStateStatus == BEHIND` | `base-behind` |
| no checks on record while `BLOCKED` | `unverifiable` |
| check rollup truncated | `unverifiable` |
| one or more blocking checks | `checks-failing` |
| one or more live review findings | `review-pending` |
| review threads exceed the fetched page | `unverifiable` |
| ready | none |

Because the ladder is an `if/elif`, exactly one branch fires, so **at most one of the four new labels is ever present**. Mutual exclusivity is a property of the derivation rather than a rule that has to be enforced and tested separately — though it is tested anyway, because a later edit could turn the ladder into independent `if`s without noticing.

### Why draft gets no label

GitHub already renders draft state as a badge in the PR list, which is the exact surface these labels exist to populate. A `draft` label would be the only managed label that duplicates something the index already shows. The cost is that a draft PR and a ready PR both carry no status label; they remain trivially distinguishable, because a ready PR carries `ready-to-merge` and a draft carries the native badge.

### Why `conflicts` is left alone

`conflicts` is granted on `mergeStateStatus == DIRTY`, which is a different signal from the `mergeable == CONFLICTING` branch of the readiness ladder. The two agree in practice and are not guaranteed to. Reusing `conflicts` as the status label for the CONFLICTING branch would quietly couple them, so the CONFLICTING branch emits no new label and `conflicts` keeps its independent meaning and its own test coverage.

## Decision 2 — inherited labels are grant-only

Status labels are added and removed. Inherited labels are added and never removed. This asymmetry is deliberate and it is the opposite of the one the chip uses.

The chip describes **live state**, so a stale one is a lie and unconditional revocation is what makes a green chip trustworthy. An inherited label describes a **judgment**, and there are two legitimate reasons a PR's judgment may differ from its issue's:

- a human deliberately re-prioritised the PR, and the next sweep must not undo that;
- the issue was relabelled after the PR opened, and rewriting the PR to match would churn its timeline on every sweep for no benefit.

Removal is therefore never correct for this group, and the source of truth stays the issue.

### The exclusive-group rule

`P1`/`P2`/`P3` are mutually exclusive, and so in practice are `bug`/`enhancement`/`documentation`. Grant-only alone would let an inherited `P1` land beside a hand-set `P2`.

So for these two groups the rule is: **grant only when the group is empty on the PR**. If the PR already carries any priority label, no priority is inherited. If it carries any kind label, no kind is inherited. Area labels are not exclusive and are granted individually if absent.

### Multiple closing issues

A PR may close several issues. Kind and area take the union. Priority takes the strongest, `P1` before `P2` before `P3`, because a PR that closes a `P1` is a `P1` regardless of what else rides with it.

## Decision 3 — the title fallback is narrow on purpose

When a PR closes no issue, kind is guessed from a conventional-commit prefix: `fix:` → `bug`, `feat:` → `enhancement`, `docs:` → `documentation`. A scoped prefix such as `fix(#492):` counts.

Everything else yields nothing. `chore:`, `refactor:`, `test:` and a bare title map to no kind rather than to a default, because a wrong kind label is worse than a missing one — it makes the index confidently misleading, and the index is the only thing this change exists to improve.

The fallback never overrides inheritance. A closing issue always wins, because it carries a human's judgment and the title carries a convention.

## Decision 4 — what is deliberately not mirrored

The issue taxonomy also has `auto-ok`, `ai-wip`, `explore`, `exploration`, `priority`, `parked`, `evidence-trial`, `needs-human`, `needs-human-session` and `needs-deploy`, plus the routing chips `sonnet` and `ultracode`.

None of these is mirrored. They fall into two groups:

- **Queue state** (`auto-ok`, `explore`, `ai-wip`, `priority`, `parked`, the routing chips) answers "should automation pick this up and how". A PR is already in flight, so the question does not arise.
- **Judgment** (`needs-human`, `needs-deploy`, `evidence-trial`) cannot be derived from a snapshot. `needs-human` on a PR would in fact be useful — a PR whose only open review thread is a design disagreement is exactly that, and one exists today — but deciding it requires reading the thread. A deterministic reconciler must not guess at it, and this change stays deterministic.

Size labels are also excluded. They are a reasonable thing for a PR to have and the issue taxonomy does not have them, so adding them here would not be mirroring the issue system; it would be inventing a second one alongside it.

## Decision 5 — where the taxonomy is written down

`docs/docs/developer_guide.md`, in the existing "Reading the PR list" section rather than a new one. That section already documents `ready-to-merge` and `conflicts` and already explains why the PR index needs labels at all, so the status group belongs in its table rather than beside it.

The **issue** taxonomy, by contrast, is documented nowhere: every one of its labels is defined only in its own GitHub description and in the nightly skills, neither of which is visible from a checkout. It is written down in the same place, because a reader arriving at either list needs the same reference, and because documenting only the newer and less-used half would be an odd thing to leave behind.

## Decision 6 — UNKNOWN mergeability gets no label, deliberately

There is one state that reads as unverifiable and does **not** receive the `unverifiable` label: mergeability GitHub has not finished computing, after the retries are exhausted.

That path returns before the ladder runs, and it is governed by an invariant the reconciler states in a comment and pins with two named tests — *"UNKNOWN mergeability is skipped, not guessed"* and *"UNKNOWN revokes an unverifiable ready-to-merge, asserts nothing new"*. On UNKNOWN it revokes the chip and asserts nothing, on the reasoning that "asserting a conflict we cannot see would be the same sin in the other direction".

Adding a label there was tried and reverted. The argument for it is decent: `unverifiable` describes the snapshot rather than the PR, so it is not a guess about mergeability. But it does add an assertion on a path whose whole contract is that it makes none, and that contract is deliberate and test-pinned rather than incidental. Overriding it to make a naming scheme tidier is the wrong trade on a script that writes to every open PR.

The practical cost is small. UNKNOWN is transient — it is what GitHub returns immediately after a push to `dev`, and the query itself is what prompts the computation — so the following sweep resolves it, within the hour at worst. A PR sits briefly with no status label and no chip, which is the same thing it does while a draft.

If this is ever revisited, the change is to that branch and to those two tests together, with the invariant restated rather than quietly dropped.

## Risk

The reconciler writes to every open PR on every sweep, so a defect here is repository-wide and immediate. Two properties bound it.

**Idempotence.** The script already diffs desired against current and calls the API only on a real difference. Widening the managed set widens what has to be diffed, and a regression would show up as label churn on every PR's timeline every hour. The self-test asserts zero write calls when the desired set already matches, and that assertion is extended to the new labels.

**Bounded ownership.** The script manages its own label set and nothing else. A label outside that set is never added or removed, so the nightly triager's writes and any human's writes are untouched. Inherited labels are the one place the two systems share names, and grant-only is what keeps that from becoming a fight.
