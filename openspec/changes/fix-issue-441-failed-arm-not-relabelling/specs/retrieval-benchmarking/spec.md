## ADDED Requirements

### Requirement: A question that failed in one arm is not counted as a bank relabelling

The benchmark comparison SHALL count a question toward a slice field's `excluded_mismatched` only when two or more arms that each ran that question to completion recorded different values for that field, so a question that merely failed or degraded in an arm is never reported as a bank edit.

`excluded_mismatched` drives a sentence that names a cause: "N question(s) carry a different
`difficulty` in different arms and were dropped from every slice of that field — the arms were
scored against different labels for the same question." A row for a question that raised
carries no bank field, so comparing its absent value against the baseline's label counted the
question as a relabelling and sent the reader to diff a question bank that never moved.

A row is run to completion when it exists and its `status` is `ok`, with an unmarked row
treated as `ok`. The comparison SHALL derive that from the same rule it uses to decide whether
a row is scorable, so there is one definition of a failed row and not two.

An arm that did not run the question to completion SHALL be **skipped in that comparison**,
not counted. The skip is per **arm**, never per question. A sweep expands into three or more
arms — `load_arms` treats one `-cd` invocation as the whole comparison — and pairing joins the
baseline with one arm at a time, so a third arm's status has no bearing on that pair.
Suppressing the whole question would therefore both hide a relabelling another arm genuinely
carries and shrink that arm's slice `n`.

A question whose **baseline** row was not run to completion SHALL be dropped from every slice
of that field, because the baseline's value is the group key and a key taken from a row that
did not run establishes nothing.

Skipping an arm SHALL NOT change any other arm's slice value, row count, mean, standard error,
verdict, or directional flag, and SHALL NOT change the slice numbers of a two-arm comparison
at all: pairing already requires the question to be scorable in both arms of the pair.

The rule SHALL apply to every slice field, and SHALL leave genuine relabelling reportable: a
question that is a clean success in the baseline and in at least one other arm, but carries
different values for the field across those arms, still counts toward `excluded_mismatched` —
whether or not some further arm failed that same question.

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

#### Scenario: A relabelling survives a third arm failing the same question

- **WHEN** a three-arm sweep is compared, one arm is a clean success on a question and labels
  it `hard` where the clean baseline labels it `easy`, and a third arm's row for that same
  question records a failure
- **THEN** `excluded_mismatched` is 1 for `difficulty`
- **AND** the third arm's failure does not suppress the count

#### Scenario: A third arm's failure does not shrink another arm's slice

- **WHEN** a three-arm sweep is compared, every arm labels a question `hard`, the baseline and
  one arm are clean successes on it, and a third arm's row for it records a failure while
  still carrying its `difficulty`
- **THEN** the clean arm's `hard` slice includes that question in its row count
- **AND** the arm that failed the question pairs only the questions it ran

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
