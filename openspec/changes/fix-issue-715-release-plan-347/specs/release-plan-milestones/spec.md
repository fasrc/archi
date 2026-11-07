## ADDED Requirements

### Requirement: The UX-override rule of the release plan names the #347 operator exception
The UX-override rule in the Method section of `docs/docs/proposals/release-plan-2026.md` SHALL name #347 and state that an operator decision of 2026-10-09 moved it from `v2026.09.0` to `v2026.11.0` so that `v2026.09.0` can ship.

The rule keeps its first sentence ("highest priority regardless of track") without change. The exception follows it in the same bullet.

#### Scenario: A reader checks the UX-override rule
- **WHEN** a reader reads the "UX overrides" bullet of the Method section
- **THEN** the bullet names #347, the date 2026-10-09, the milestones `v2026.09.0` and `v2026.11.0`, and the reason that `v2026.09.0` can ship

#### Scenario: The exception links the issue
- **WHEN** `grep -n "One exception (2026-10-09)" docs/docs/proposals/release-plan-2026.md` runs
- **THEN** it prints exactly one line, and the line after it holds `[#347](https://github.com/fasrc/archi/issues/347)`

### Requirement: The release plan records the move of #347 where it scheduled #347
The 2026-08-25 scheduling paragraph of `docs/docs/proposals/release-plan-2026.md` SHALL follow the sentence that schedules #347 into `v2026.09.0` with a sentence that records the 2026-10-09 operator decision that moved #347 to `v2026.11.0`, and its reason.

No line of the plan schedules #347 into `v2026.09.0` without a record of the move in the same paragraph. The #347 row, the other rows, and the GitHub milestone of #347 do not change.

#### Scenario: A reader follows the scheduling paragraph
- **WHEN** a reader reads the paragraph that starts "**Six scheduled.**"
- **THEN** the sentence after "September is the earliest that is." says that an operator decision of 2026-10-09 moved #347 to `v2026.11.0` so that `v2026.09.0` can ship

#### Scenario: Every milestone that the plan gives #347 agrees with the tracker
- **WHEN** `grep -n "347" docs/docs/proposals/release-plan-2026.md` runs and the GitHub milestone of #347 is read
- **THEN** each line that names a milestone for #347 names `v2026.11.0` as the current milestone, or names `v2026.09.0` only in a paragraph that also records the move to `v2026.11.0`, and the GitHub milestone is `v2026.11.0`

#### Scenario: The edit changes only the plan
- **WHEN** `git diff --name-only origin/dev -- . ':!openspec/changes/fix-issue-715-release-plan-347'` runs on the fix branch
- **THEN** it prints only `docs/docs/proposals/release-plan-2026.md`
