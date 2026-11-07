## Context

The release plan is pinned (`CLAUDE.md`, "Release plan (pinned)"), so an agent must not
choose between the two options of the Codex finding. The operator chose "keep the
exception" on 2026-10-20, and issue #715 records it in its "Decision" section. The
issue's line numbers (88-90, 169, 294-297) are for `82850b3b`. On `origin/dev`
`73104693` the same text is at lines 89-91, 175, and 302-306, because PRs #736, #760 and
#761 added lines above it. Match the text, not the line numbers.

The Method section already holds one recorded exception with the same shape: "One
amendment (2026-08-28): the WordPress chat window (#384) enters `v2026.10.0` by operator
decision — the single exception to the frontends line. On 2026-10-23 an operator
decision moved the window to `v2026.11.0`." The new sentences copy that style: a date, an
operator decision, the old and new milestone, and the reason.

## Goals / Non-Goals

**Goals:**
- The UX-override rule names the #347 exception.
- The scheduling paragraph records the move to `v2026.11.0` and its reason.
- No line of the plan leaves #347 scheduled into `v2026.09.0` with no later record of
  the move.

**Non-Goals:**
- No change to the #347 row (line 175). It already records the move.
- No change to any GitHub milestone, and no change to #347 itself.
- No change to line 314 ("the twin of scheduled #347"). It names no milestone.
- No new accounting entry and no change to the header or the TL;DR. The decision asks
  for one sentence in each of the two places only.

## Decisions

### D1. The UX-override bullet (Method)

Replace the 3 lines

```text
- **UX overrides:** anything an end chat user experiences (leaked reasoning, wrong
  citations, overflow apologies, silently stale knowledge base) is highest priority
  regardless of track.
```

with the 5 lines

```text
- **UX overrides:** anything an end chat user experiences (leaked reasoning, wrong
  citations, overflow apologies, silently stale knowledge base) is highest priority
  regardless of track. One exception (2026-10-09): an operator decision moved
  [#347](https://github.com/fasrc/archi/issues/347) (a literal `</think>` truncates an
  answer) from `v2026.09.0` to `v2026.11.0` so that `v2026.09.0` can ship.
```

The link uses the same form as the plan's other issue links. The rendered HTML shows
the code span as `</think>` (checked with `mkdocs build --strict` on a scratch copy).

### D2. The scheduling paragraph (2026-08-25 accounting)

Replace the 2 lines

```text
or a first-tag-only rule — an open design question — so August is not feasible and
September is the earliest that is. #326, #327 and #330 into `v2026.11.0`, each gating
```

with the 4 lines

```text
or a first-tag-only rule — an open design question — so August is not feasible and
September is the earliest that is. On 2026-10-09 an operator decision moved #347 to
`v2026.11.0` so that `v2026.09.0` can ship; Method records it as the one exception to
the UX override. #326, #327 and #330 into `v2026.11.0`, each gating
```

The sentence "#347 into `v2026.09.0` by the UX override" stays. It is the record of the
2026-08-25 decision, and the plan keeps its accounting entries as dated history. The new
sentence follows it at once, so a reader of the paragraph sees the move.

### D3. One script applies both edits

The script in the `tasks.md` appendix replaces each old block with its new block. It
exits non-zero if an old block does not match exactly once, so a second run, or a run on
a changed file, fails loudly and writes nothing. Loop 1 ran it on a scratch copy of the
plan at `73104693`: it applied both edits (`git diff --stat`: 6 insertions, 2
deletions), and a second run exited 1 with "expected 1 match, found 0".

## Risks / Trade-offs

- **Line numbers drift** if another PR edits the plan above these lines first. The
  script matches text, not line numbers, so drift does not matter. A changed old block
  makes the script fail, and then the task stops for a human.
- **A docs link break** can pass the gate, because the gate does not build the docs.
  `mkdocs build --strict` exits 0 even with broken links, so the check reads its `INFO`
  lines. Loop 1 measured 6 filtered `INFO` lines before and 6 identical lines after the
  edit (the 4 known broken anchors, the "not in nav" note, and the `/ssb/status` note).
