## Why

The pinned release plan (`docs/docs/proposals/release-plan-2026.md`) does not agree with
itself about #347 (a literal `</think>` in an answer truncates it). Codex posted the
finding on PR #658 (thread `r4233874792`) ten minutes before #658 merged, and the nightly
review filed it as #715.

On `origin/dev` `73104693`:

1. **The #347 row (line 175)** is in the `v2026.11.0` table and says "Moved from
   `v2026.09.0` by operator decision (2026-10-09) so that release can ship". The GitHub
   milestone of #347 is `v2026.11.0`.
2. **The UX-override rule (Method, lines 89-91)** says that anything an end chat user
   experiences "is highest priority regardless of track". It names no exception, so the
   move of #347 breaks the plan's own rule.
3. **The scheduling paragraph of the 2026-08-25 accounting (lines 302-306)** says "#347
   into `v2026.09.0` by the UX override" and that September is the earliest feasible
   release. No later sentence records the move.

The operator decided on 2026-10-20 (issue #715, "Decision" section): **keep the
exception**. #347 stays in `v2026.11.0`. The UX-override rule and the scheduling
paragraph each get one sentence that names #347 and the 2026-10-09 exception. No
milestone changes.

**This change is a driver for the nightly executor only.** The plan document is the
design of record. The nightly supervisor reads its task list from
`openspec/changes/fix-issue-<n>-<slug>/tasks.md`, so this folder exists to hold that
list. Issue #715 says "change only `docs/docs/proposals/release-plan-2026.md`". This
folder is the one exception, and the PR body says so.

## What Changes

- **Docs only.** One file changes outside this folder:
  `docs/docs/proposals/release-plan-2026.md`. No code, no test, no config.
- The UX-override bullet in Method gets one sentence: the 2026-10-09 operator decision
  moved #347 from `v2026.09.0` to `v2026.11.0` so that `v2026.09.0` can ship.
- The scheduling paragraph of the 2026-08-25 accounting gets one sentence after
  "September is the earliest that is.": the 2026-10-09 move to `v2026.11.0`, its reason,
  and a pointer to the exception in Method.
- **No** change to the #347 row, to any other row, or to any GitHub milestone. The old
  sentence "#347 into `v2026.09.0`" stays, because it is the dated record of the
  2026-08-25 decision. The new sentence follows it in the same paragraph.

## Capabilities

### New Capabilities
- `release-plan-milestones`: what the pinned release plan states about an issue that an
  operator decision moved out of the milestone that a plan rule gives it.

### Modified Capabilities
<!-- None. -->

## Impact

- `docs/docs/proposals/release-plan-2026.md` (Method "UX overrides" bullet; the
  2026-08-25 accounting's scheduling paragraph).
- This folder.
- One reply on the Codex thread of PR #658 (`4233874792`) that names the fix commit.
