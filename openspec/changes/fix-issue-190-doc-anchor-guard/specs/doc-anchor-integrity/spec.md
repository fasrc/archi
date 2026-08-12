## ADDED Requirements

### Requirement: Every documented line number SHALL be verified against the code it names
Every line number that `docs/docs/api_reference.md` cites into `src/interfaces/chat_app/app.py` SHALL be verified by a test that the gate runs, by reading the line at that number and asserting it contains the substring recorded for it, so that no anchored line number in the document is unverified regardless of which citation form expresses it.

#### Scenario: A link definition is verified
- **WHEN** the document defines `[tag]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L<n>`
- **THEN** the line at `<n>` in `app.py` is read and asserted to contain the substring recorded for that anchor

#### Scenario: An inline citation is verified in its own right
- **WHEN** the document contains an inline citation naming a line number
- **THEN** that number is verified against `app.py` independently of the link definition its tag resolves to

#### Scenario: Coverage is complete
- **WHEN** the set of verified line numbers is compared against every line number the document cites into `app.py`
- **THEN** the two sets are equal, and a citation the guard cannot parse is a failure rather than a silent skip

### Requirement: A drifted anchor SHALL fail the gate and name what drifted
A stale anchor SHALL cause `bash scripts/gate.sh` to exit non-zero with a message naming the offending anchor, the line number it claims, the substring expected there, and the content actually found, so the failure is actionable without reading the test.

#### Scenario: Lines shift under an anchor
- **WHEN** a blank line is inserted near the top of `ChatWrapper.stream`, shifting the lines every anchor below it names
- **THEN** the gate fails
- **AND** the failure message names at least one stale anchor together with its expected and actual content

#### Scenario: Correct anchors do not fail
- **WHEN** every anchor names a line whose content matches its recorded substring
- **THEN** the guard passes and contributes no failure to the gate

### Requirement: Canonical inline citations SHALL match their link definition
An inline citation written in the canonical form `` [`app.py:NNNN`][tag] `` SHALL name the same line number as `[tag]`'s link definition, so the two spellings of the same anchor cannot disagree.

#### Scenario: Canonical citation agrees with its definition
- **WHEN** the document contains `` [`app.py:2418`][thinkgate] `` and `[thinkgate]` is defined at `#L2418`
- **THEN** the consistency assertion passes

#### Scenario: Canonical citation disagrees with its definition
- **WHEN** a canonical citation names a line number different from its tag's link definition
- **THEN** the guard fails and names the tag together with both numbers

### Requirement: Abbreviated inline citations SHALL be free to cite a different line
An inline citation written in the abbreviated form `` [`:NNNN`][tag] `` SHALL be permitted to name a line number other than its tag's link definition, because the document uses that spelling to cite a specific line within the region the tag points at, and it SHALL still be verified against `app.py` by the content assertion.

#### Scenario: Abbreviated citation names the event literal rather than the gate
- **WHEN** the event table cites `` [`:2412`][thinkgate] `` while `[thinkgate]` is defined at `#L2418`
- **THEN** the guard does not report a mismatch
- **AND** line 2412 is still asserted to contain its recorded substring

#### Scenario: Abbreviated citation drifts
- **WHEN** an abbreviated citation names a line whose content no longer matches its recorded substring
- **THEN** the guard fails and names that citation

### Requirement: Range citations SHALL pin both endpoints
An inline citation naming a line range SHALL have both of its endpoints verified against `app.py`, and its tag's link definition SHALL equal one of the two endpoints, so a range cannot drift at either end nor detach from the anchor it resolves through.

#### Scenario: A range citation is verified
- **WHEN** the document contains `` [`app.py:2435-2441`][chunkyield] `` and `[chunkyield]` is defined at `#L2441`
- **THEN** lines 2435 and 2441 are each asserted to contain their recorded substrings
- **AND** the definition line matching the range's end satisfies the endpoint rule

### Requirement: The anchor-maintenance policy SHALL be written down
The rule that a change shifting lines in `src/interfaces/chat_app/app.py` must also update the anchors in `docs/docs/api_reference.md` SHALL be recorded in `AGENTS.md`, so a contributor meeting the guard for the first time finds the policy rather than inferring it from a test failure.

#### Scenario: A contributor looks for the rule
- **WHEN** `AGENTS.md` is read
- **THEN** it states that PRs shifting lines in `app.py` must update the `api_reference.md` anchors
- **AND** it names the test that enforces this
