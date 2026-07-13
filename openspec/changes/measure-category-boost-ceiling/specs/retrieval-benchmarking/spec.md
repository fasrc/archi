## MODIFIED Requirements

### Requirement: Grounded FASRC question banks in the harness schema

The system SHALL provide FASRC question banks consumable by the benchmark harness
(`queries_path`), where every record carries a `question` and, for RAGAS scoring, an
`answer` whose content is grounded in a real source rather than fabricated. At least one
bank SHALL tag records by question type so results can be sliced by difficulty
(simple retrieval vs multi-step reasoning vs out-of-scope refusal).

A bank SHALL additionally provide gold sources drawn from **every source group present in
the ingested corpus**, not only the dominant one. A bank whose gold sources all belong to a
single source group cannot detect a treatment that improves that group by demoting another,
so such a treatment would measure as a clean win while silently regressing the rest of the
corpus. Concretely: the FASRC corpus comprises the `docs.rc.fas.harvard.edu` Knowledge Base
*and* non-KB sources (`slurm.schedmd.com`, the namesake wiki page), so a bank used to
evaluate a retrieval treatment SHALL include questions whose gold source is a non-KB
document.

#### Scenario: Bank loads against the harness contract

- **WHEN** the harness loads a provided question bank for a RAGAS-mode run
- **THEN** every record exposes the required `question` and `answer` fields and the load does not raise a missing-field error

#### Scenario: Results can be sliced by question type

- **WHEN** a typed bank is used and results are analyzed
- **THEN** quality metrics can be reported separately for retrieval-only, reasoning, and should-refuse questions, so the analysis can show which question type the treatment affects

#### Scenario: Out-of-scope questions test refusal, not recall

- **WHEN** a should-refuse question (covering a system outside the FASRC corpus) is scored
- **THEN** the expected answer is a referral/acknowledgement of the gap, so a confident fabricated answer is counted as a failure

#### Scenario: Every corpus source group is represented by gold sources

- **WHEN** a bank is used to evaluate a retrieval treatment against the FASRC corpus
- **THEN** its gold sources include at least one non-KB document (e.g. a `slurm.schedmd.com` page), so a treatment that lifts KB recall by demoting non-KB documents is visible rather than invisible

#### Scenario: Single-source-group bank is rejected for treatment evaluation

- **WHEN** a bank whose gold sources all belong to one source group is used to justify a retrieval treatment
- **THEN** the result is treated as unsound for that purpose, because the bank is structurally incapable of observing harm to the unrepresented groups
