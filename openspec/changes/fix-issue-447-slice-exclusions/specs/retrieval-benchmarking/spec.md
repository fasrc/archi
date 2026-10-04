## ADDED Requirements

### Requirement: The slice re-labelling count is reported at the top level of the comparison

The benchmark comparison SHALL report, for every slice field that every arm carries, the number of questions excluded because the arms that ran them recorded different labels, as `slice_exclusions` at the top level of the report, whether or not any slice row is emitted for that field.

The count SHALL be present for each such field, zero included, in both the `--json` output and
the data the rendered report is built from. Each emitted slice row SHALL keep its
`excluded_mismatched` value, equal to the top-level count for its field.

The rendered report SHALL print the "carry a different" sentence for every field with a
non-zero count, including when no slice row exists. It SHALL print "No slice field (…) is
present in every arm." only when no slice field is carried by every arm.

#### Scenario: Every question is re-labelled

- **WHEN** two clean arms label every question differently for `difficulty`
- **THEN** no `difficulty` slice row is emitted
- **AND** `slice_exclusions["difficulty"]` equals the number of questions
- **AND** the rendered report contains the "carry a different" sentence and not "No slice field"

#### Scenario: A group survives but scores nothing

- **WHEN** a group of agreeing questions exists but every metric pairs `n == 0`
- **THEN** `slice_exclusions` still reports the re-labelled count for that field

#### Scenario: No disagreement

- **WHEN** every arm carries a slice field and all labelled clean arms agree
- **THEN** `slice_exclusions` holds that field with the value 0

### Requirement: An unlabelled row is skipped in the re-labelling comparison

The benchmark comparison SHALL compare only the clean arms whose row carries a label for the field, so a row with no label (absent, `null`, or empty string) is never counted as a disagreement, and the count is the same whichever arm is the baseline.

A question whose baseline row has no string label SHALL still be dropped from every slice
group, because the baseline's label is the group key.

#### Scenario: The baseline carries no label and two other arms disagree

- **WHEN** three clean arms run a question, the baseline's row has no label, and the other two disagree
- **THEN** the count for that field is 1
- **AND** the count is 1 with each of the three arms as the baseline

#### Scenario: An unlabelled row next to agreeing labelled rows

- **WHEN** one clean arm has no label and the other labelled clean arms agree
- **THEN** the question does not count as a disagreement
