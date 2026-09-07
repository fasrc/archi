## ADDED Requirements

### Requirement: A question that failed in one arm is not counted as a bank relabelling

The benchmark comparison SHALL count a question toward a slice field's `excluded_mismatched` only when every arm ran that question to completion and the arms recorded different values for that field, so a question that merely failed or degraded in one arm is never reported as a bank edit.

`excluded_mismatched` drives a sentence that names a cause: "N question(s) carry a different
`difficulty` in different arms and were dropped from every slice of that field — the arms were
scored against different labels for the same question." A row for a question that raised
carries no bank field, so comparing its absent value against the baseline's label counted the
question as a relabelling and sent the reader to diff a question bank that never moved.

A row is run to completion when it exists and its `status` is `ok`, with an unmarked row
treated as `ok`. The comparison SHALL derive that from the same rule it uses to decide whether
a row is scorable, so there is one definition of a failed row and not two.

A question that any arm did not run to completion SHALL be dropped from every slice of that
field and SHALL NOT be counted, because it cannot contribute a paired delta: pairing already
requires the question to be scorable in both arms. Dropping it SHALL NOT change any slice's
value, row count, mean, standard error, verdict, or directional flag.

The rule SHALL apply to every slice field, and SHALL leave genuine relabelling reportable: a
question that is a clean success in every arm but carries different values for the field still
counts toward `excluded_mismatched`.

#### Scenario: A question that failed in one arm, read from an artifact written before the producer fix

- **WHEN** two arms are compared, three questions are paired, and the treatment arm's row for one of them records a failure and carries neither `difficulty` nor `anchor_type`
- **THEN** `excluded_mismatched` is 0 for `difficulty` and 0 for `anchor_type`
- **AND** the rendered report contains no sentence saying a question carries a different `difficulty` or `anchor_type` in different arms

#### Scenario: A degraded row is not a relabelling either

- **WHEN** an arm's row for a paired question records `status` as `degraded` and omits the bank field
- **THEN** `excluded_mismatched` is 0 for that field
- **AND** the question appears in no slice of that field

#### Scenario: Genuine relabelling is still counted

- **WHEN** two arms are compared and one question is a clean success in both arms but is labelled `easy` in the baseline and `hard` in the treatment arm
- **THEN** `excluded_mismatched` is 1 for `difficulty`
- **AND** only the slice for the value both arms agree on is reported

#### Scenario: Dropping a failed question moves no slice number

- **WHEN** the same two arms are compared before and after this change
- **THEN** every slice reports the same field, value, row count, mean, standard error, verdict, and directional flag
- **AND** `excluded_mismatched` is the only value that differs

#### Scenario: A field that only some rows carry is still sliced

- **WHEN** one arm's rows carry `difficulty` on every clean row and omit it on a failure row
- **THEN** the field is still sliced rather than skipped, and the slices the clean rows support are reported

#### Scenario: One definition of a failed row

- **WHEN** a row is absent, or records a `status` other than `ok`
- **THEN** the comparison treats it as not run to completion for slice membership and as not scorable for pairing, from the same rule
- **AND** a row with no `status` key is treated as run to completion
