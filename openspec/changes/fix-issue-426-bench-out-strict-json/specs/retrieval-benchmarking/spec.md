## ADDED Requirements

### Requirement: Committed benchmark artifacts are strict JSON with honest scored denominators

Every benchmark artifact committed under `bench_out/` SHALL parse under a JSON reader that rejects `NaN` and `Infinity`, and each arm's `<metric>_scored` denominator SHALL report the count of finite per-question scores for that metric over the arm's scorable rows.

The harness has written artifacts this way since #279: `json_safe`
(`src/utils/benchmark_schema.py:603`) copies every non-finite float to `null`, `ResultHandler.dump`
(`src/bin/service_benchmark.py:555`) dumps with `allow_nan=False`, and
`score_metrics_per_eligibility` (`src/utils/benchmark_schema.py:594`) counts only the finite scores
that reached an aggregate. This requirement states the same contract for the artifacts at rest,
which is where a reader meets it.

It is worth a requirement rather than a one-time correction because both defects are silent to the
repository's own tooling. Every loader here tolerates a bare `NaN`, so nothing in CI notices; the
failure surfaces only in a strict reader outside the repository, or in a human reading a
denominator. An inflated denominator is worse than a missing one: it answers the exact question
§3.4 of the interpreting guide tells a reader to ask before trusting an aggregate, and it answers
it wrongly with full confidence.

"Scorable rows" carries the same meaning it has at write time (`is_scorable`,
`src/utils/benchmark_resilience.py:54`): a row whose `status` is `ok` or absent. "Finite scores"
excludes a non-finite cell of any spelling and excludes a boolean, but includes a genuine `0.0` —
"the judge produced no score" and "the judge scored it zero" are different findings and only one of
them is bad news.

#### Scenario: A committed artifact is read by a strict parser

- **WHEN** a committed `bench_out/*.json` artifact is read with `json.loads` and a `parse_constant` that raises
- **THEN** the read succeeds
- **AND** no bare `NaN` or `Infinity` token appears anywhere in the file

An unscored cell is `null`, not a bare `NaN`. `null` rather than a raise, because the run's other
scores are worth keeping; `null` rather than `0.0`, because that would report a judgement the judge
never made.

#### Scenario: A scored denominator is checked against the per-question cells

- **WHEN** an arm's `<metric>_scored` is compared against that arm's `single_question_results`
- **THEN** its first number equals the count of rows whose `status` is `ok` or absent and whose `<metric>` cell is a finite number
- **AND** its second number equals the count of rows whose `status` is `ok` or absent

A row the metric was never eligible on carries no cell for that metric, so it contributes to the
second number and not the first. That is the intended reading: the denominator is the run's
scorable size, and the gap between the two numbers is what the reader is being told about.

#### Scenario: A rendered report carries no unscored-cell literal

- **WHEN** a committed `_report.md` or `_report.html` under `bench_out/` is searched for the word `nan`
- **THEN** it is not found

A report is a view of its artifact, so a corrected artifact whose report was not re-rendered still
shows the reader the old, invalid number. The report is where a human meets this data, which makes
a stale report the more misleading of the two.

#### Scenario: A regression test enforces both invariants over every committed artifact

- **WHEN** the unit-test suite runs
- **THEN** a test asserts the strict parse and the denominator equality over every artifact committed under `bench_out/`, and fails if any artifact violates either

The migration alone is a one-time byte edit. Nothing would stop an artifact committed from an older
harness, or a hand-corrected denominator, from reintroducing the defect — and nothing in the
repository would report it, because every loader here reads the broken form without complaint.
