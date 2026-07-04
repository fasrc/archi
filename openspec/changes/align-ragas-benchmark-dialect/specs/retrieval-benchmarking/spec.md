## MODIFIED Requirements

### Requirement: Grounded FASRC question banks in the harness schema

The system SHALL provide FASRC question banks consumable by the benchmark harness
(`queries_path`) in ragas 0.3.5's modern schema, where every record carries a
`user_input` (the query) and, for RAGAS scoring, a `reference` (ground-truth
answer) whose content is grounded in a real source rather than fabricated. The
harness SHALL also accept banks authored in the legacy `question`/`answer` schema
by normalizing them on read (`question→user_input`, `answer→reference`,
`contexts→retrieved_contexts`), so existing and externally-supplied banks continue
to load. At least one bank SHALL tag records by question type (`anchor_type`) so
results can be sliced by difficulty (simple retrieval vs multi-step reasoning vs
out-of-scope refusal).

#### Scenario: Modern bank loads against the harness contract

- **WHEN** the harness loads a bank whose records use `user_input` and (for RAGAS mode) `reference`
- **THEN** every record exposes the required fields and the load does not raise a missing-field error

#### Scenario: Legacy bank is normalized on read

- **WHEN** the harness loads a bank whose records use the legacy `question`/`answer` schema
- **THEN** each record is normalized to `user_input`/`reference` — the ground-truth answer mapping to `reference` (never `response`) — before scoring, and the run proceeds without error

#### Scenario: Results can be sliced by question type

- **WHEN** a typed bank is used and results are analyzed
- **THEN** quality metrics can be reported separately for retrieval-only, reasoning, and should-refuse questions, so the analysis can show which question type the treatment affects

#### Scenario: Out-of-scope questions test refusal, not recall

- **WHEN** a should-refuse question (covering a system outside the FASRC corpus) is scored
- **THEN** the expected answer is a referral/acknowledgement of the gap, so a confident fabricated answer is counted as a failure

## ADDED Requirements

<!--
  Scope note: this change owns the ragas *dialect/schema* contract AND the
  per-metric eligibility that follows from it. Run-status resilience (failed/degraded
  row isolation, keyed per-question attribution, all-fail aggregation) is owned by
  the sibling `benchmark-run-resilience` capability (change
  harden-benchmark-and-agent-resilience, PR #92) and provides the scorable candidate
  set this change scores over. Requirements below deliberately do NOT restate keyed
  per-question attribution. They DO own per-metric scored denominators, because the
  sibling's whole-column `build_ragas_aggregates` (a single skip-NaN mean per metric)
  structurally cannot express a different denominator for context vs answer metrics.
-->

### Requirement: RAGAS scoring uses the ragas 0.3.5 EvaluationDataset contract

The harness SHALL construct the RAGAS scoring input as a `ragas.EvaluationDataset`
of `SingleTurnSample` records keyed `user_input`/`retrieved_contexts`/`response`/
`reference`, matching the pinned `ragas==0.3.5` API. The agent's generated answer
SHALL populate `response` and the retrieved documents SHALL populate
`retrieved_contexts`; the bank's ground-truth answer SHALL populate `reference`.
archi extension fields (`sources`, `source_match_field`, `anchor_type`, `notes`)
SHALL NOT be passed into the ragas records.

#### Scenario: Scoring input matches the installed ragas API

- **WHEN** a RAGAS-mode run builds its scoring input
- **THEN** it produces a ragas `EvaluationDataset` with the modern column names and no legacy `question`/`contexts`/`answer`/`ground_truth` columns

#### Scenario: Extension fields are excluded from ragas

- **WHEN** a record carries `sources`, `source_match_field`, `anchor_type`, or `notes`
- **THEN** those fields are consumed by SOURCES mode and difficulty slicing directly, and are absent from the ragas `SingleTurnSample`

### Requirement: Per-metric row eligibility for empty required columns

The harness SHALL score each RAGAS metric only over rows whose required columns are
populated. When a row's `reference` is empty (for example an intentional
should-refuse question), `context_precision` and `context_recall` SHALL exclude
that row. Each metric SHALL be scored over its own eligible `EvaluationDataset`, so
its aggregate is the mean over the eligible rows — **not** a skip-NaN mean over the
full set — and the harness SHALL report each metric's scored denominator
(`n_scored / n_total`). This data-emptiness eligibility composes with the run-status
filtering (failed/degraded rows) owned by the `benchmark-run-resilience` capability,
which supplies the scorable candidate set the eligibility is applied on top of.

#### Scenario: Empty-reference rows are excluded from context metrics

- **WHEN** a bank contains rows with an empty `reference`
- **THEN** those rows are excluded from `context_precision` and `context_recall` scoring rather than contributing a hidden NaN to the mean

#### Scenario: Answer metrics still score reference-free rows

- **WHEN** a row has a populated `response` but an empty `reference`
- **THEN** `answer_relevancy` and `faithfulness` still score that row, because they do not require `reference`

#### Scenario: Each metric reports its scored denominator

- **WHEN** a context metric is scored with some rows excluded for an empty `reference`
- **THEN** its aggregate is the mean over the eligible rows and the scored denominator (`n_scored / n_total`) is reported, without relying on a skip-NaN mean over the full set

#### Scenario: Eligibility applies on top of the scorable set

- **WHEN** the run-resilience layer has already excluded failed/degraded rows from the scorable set
- **THEN** per-metric eligibility is applied to that scorable set (not the raw bank), so status-excluded and empty-`reference` rows are both absent from a context metric's denominator
