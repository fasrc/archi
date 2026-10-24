## Why

The `ragas-goldenset-maintenance` spec requires confirmation before a lock, but never defines
"confirmed" (issue #213). On `origin/dev` `82850b3b`, in
`openspec/specs/ragas-goldenset-maintenance/spec.md`:

- `:6` (*Machine-queryable confirmation state*) defines only what to record: `status`,
  `source_hashes`. `:16` lets a source-less row lock with no `source_hashes`.
- `:29`, `:36` and `:200-202` (*Human-gated mutation*, `:187`) say "confirms and locks", but not
  what the human checks. No open change defines it.

So two reviewers can lock one row by different standards, and a locked `reference` can assert a
fact that the corpus does not hold. RAGAS `answer_correctness` and `factual_correctness` score
the answer against the whole `reference`, so such a fact lowers the score of a correct answer.

The operator answered the five open questions on 2026-10-19 (#213 body, "Decision").

## What Changes

- ADD one requirement, *Confirmation standard for locking*, to the `ragas-goldenset-maintenance`
  capability. It states the operator's five decisions:
  1. every discrete fact in the `reference` must be found, not only the facts that `user_input`
     asks about;
  2. partial support: trim, then lock; a row is never locked while it asserts an unsupported fact;
  3. stale `notes`: fix the notes, then lock;
  4. the evidence source is the ingested chunks, not the live web page;
  5. a lock batch shows, per fact, the source URL, the chunk index, and a quoted span.
- The requirement keeps source-less rows (for example `should_refuse` anchors) under the existing
  rule (`spec.md:16`, scenario at `:36`).
- Five scenarios, one per decision.
- Archive the change, so the requirement merges into
  `openspec/specs/ragas-goldenset-maintenance/spec.md` and the change moves to
  `openspec/changes/archive/`.

Name: the issue's plan calls the change `define-goldenset-confirmation`. The nightly pipeline
drives its loop from `openspec/changes/fix-issue-<n>-<slug>/tasks.md`, so this change is
`fix-issue-213-define-goldenset-confirmation`. The validate command in acceptance criterion 5
uses that name.

Out of scope (issue #213): verifying or locking any bank row, any edit to
`benchmarking/fasrc_ragas_queries.json` (the bank is in `fasrc/archi-config`; that work is
`fasrc/archi-config#19`), any change to drift, coverage, orphan detection or other code, and any
comment or edit in `fasrc/archi-config`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ragas-goldenset-maintenance`: one ADDED requirement. No existing requirement text changes, so
  the delta uses `## ADDED Requirements` and not `MODIFIED` (issue constraint).

## Impact

- Specs: `openspec/specs/ragas-goldenset-maintenance/spec.md` gets the new requirement at archive
  time.
- Code, tests, config, and the golden-set bank: no change.
- Process: lock batches in `fasrc/archi-config#19` are reviewed against this requirement. After
  the merge, a human comments on `fasrc/archi-config#19` with a link to the merged requirement.
- No tool enforces the standard yet. It is a standard for the human reviewer who confirms a lock.
