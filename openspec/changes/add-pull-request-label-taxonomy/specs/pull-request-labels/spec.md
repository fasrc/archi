## ADDED Requirements

### Requirement: Exactly one status label explains a withheld readiness chip

The reconciler SHALL add at most one status label to an open pull request, derived from the same precedence ladder that decides `ready-to-merge`, so the index shows why the chip was withheld. The status labels are `review-pending`, `checks-failing`, `checks-pending`, `base-behind` and `unverifiable`. A check that has not finished MUST be reported as pending rather than as failing, and a check that has finished badly outranks one still running. A pull request that is ready, that is a draft, or whose blocker is a merge conflict MUST carry none of the five: a ready PR carries `ready-to-merge`, a draft is rendered as a draft by GitHub, and a conflict is already reported by the existing `conflicts` label. A status label MUST be removed as soon as its cause no longer holds.

#### Scenario: Live review findings earn `review-pending`

- **WHEN** a pull request is not a draft, has no merge conflict, has no blocking check, and has one or more unresolved review threads
- **THEN** `review-pending` is added
- **AND** `ready-to-merge` is not present
- **AND** none of `checks-failing`, `checks-pending`, `base-behind`, `unverifiable` is present

#### Scenario: A running check is pending, not failing

- **WHEN** a pull request has a check that has not finished, and none that has finished badly
- **THEN** `checks-pending` is added
- **AND** `checks-failing` is NOT added
- **AND** `ready-to-merge` is still withheld, exactly as before the two were distinguished

#### Scenario: A finished failure outranks a running check

- **WHEN** a pull request has one check that finished badly and one still running
- **THEN** `checks-failing` is added
- **AND** `checks-pending` is NOT added

#### Scenario: Neutral and skipped checks remain passing

- **WHEN** every check on a pull request concluded `NEUTRAL` or `SKIPPED`
- **THEN** neither `checks-pending` nor `checks-failing` is added
- **AND** `ready-to-merge` is granted, provided the rest of the predicate holds

#### Scenario: A blocking check outranks a live finding

- **WHEN** a pull request has both a failing check and an unresolved review thread
- **THEN** `checks-failing` is added
- **AND** `review-pending` is NOT added, because the ladder reports one reason and the check is the earlier branch

#### Scenario: Resolving the last thread clears the label

- **WHEN** a pull request holds `review-pending` and its last unresolved thread becomes resolved
- **THEN** `review-pending` is removed
- **AND** `ready-to-merge` is added, provided the rest of the predicate holds

#### Scenario: A draft carries no status label

- **WHEN** a pull request is a draft, whatever else is true of it
- **THEN** none of the five status labels is added
- **AND** any status label it already held is removed

#### Scenario: A conflicted pull request keeps only `conflicts`

- **WHEN** a pull request reports a merge conflict
- **THEN** the existing `conflicts` label is applied unchanged
- **AND** none of the five new status labels is added

#### Scenario: An unverifiable snapshot is labelled as such, never as ready

- **WHEN** the review-thread connection is truncated, or the check rollup is truncated, or there are no checks on record while GitHub reports the base as blocked
- **THEN** `unverifiable` is added
- **AND** `ready-to-merge` is not granted, and is revoked if held

### Requirement: A pull request inherits kind, priority and area from the issues it closes

The reconciler SHALL add to a pull request the kind, priority and area labels carried by the issues that pull request closes, and SHALL never remove a label of those groups. Priority is `P1`, `P2` or `P3`; kind is `bug`, `enhancement` or `documentation`; area is any of the repository's area labels. Priority and kind are exclusive groups and are inherited only when the pull request carries no label from that group. Area labels are inherited individually when absent. When several issues are closed, kind and area take the union and priority takes the strongest.

#### Scenario: A PR closing a P3 bug inherits both

- **WHEN** a pull request declares `Closes #491` and issue 491 carries `bug` and `P3`
- **THEN** `bug` and `P3` are added to the pull request

#### Scenario: A hand-set priority is not overridden

- **WHEN** a pull request already carries `P1` and closes an issue carrying `P3`
- **THEN** `P3` is NOT added
- **AND** `P1` is left in place

#### Scenario: An inherited label is never revoked

- **WHEN** a pull request carries an inherited `P2` and the issue it closes is relabelled to `P3`
- **THEN** the next sweep leaves `P2` in place and adds nothing

#### Scenario: The strongest priority wins across several closing issues

- **WHEN** a pull request closes one issue carrying `P3` and another carrying `P1`
- **THEN** `P1` is added
- **AND** `P3` is not added

#### Scenario: Area labels accumulate

- **WHEN** a pull request closes one issue carrying `ragas` and another carrying `upstream`
- **THEN** both `ragas` and `upstream` are added

### Requirement: Kind falls back to the title only when no issue is closed

The reconciler SHALL derive a kind label from a conventional-commit prefix on the pull request title when the pull request closes no issue, mapping `fix` to `bug`, `feat` to `enhancement` and `docs` to `documentation`, with an optional scope such as `fix(#492):`. Any other prefix, and a title with no prefix, MUST yield no kind label rather than a default. The fallback MUST NOT apply when the pull request closes an issue, and MUST NOT apply when the pull request already carries a kind label.

#### Scenario: A scoped fix prefix yields `bug`

- **WHEN** a pull request closes no issue and is titled `fix(#492): harden the tar forcing guard`
- **THEN** `bug` is added

#### Scenario: An unrecognized prefix yields nothing

- **WHEN** a pull request closes no issue and is titled `chore: archive the otel change`
- **THEN** no kind label is added

#### Scenario: A closing issue outranks the title

- **WHEN** a pull request is titled `docs: ...` and closes an issue carrying `bug`
- **THEN** `bug` is added
- **AND** `documentation` is not added

### Requirement: The reconciler owns only its own labels and writes only on a difference

The reconciler SHALL restrict every add and remove to its managed label set, and SHALL issue no write call for a pull request whose labels already match the desired set. A label outside the managed set MUST be left untouched, so that labels applied by the nightly triager or by a human are never disturbed.

#### Scenario: A matching pull request costs no write

- **WHEN** a sweep runs over a pull request that already carries exactly the labels the reconciler would set
- **THEN** no label write call is issued for that pull request

#### Scenario: An unmanaged label survives a sweep

- **WHEN** a pull request carries `needs-deploy`, which the reconciler does not manage
- **THEN** the sweep leaves `needs-deploy` in place
