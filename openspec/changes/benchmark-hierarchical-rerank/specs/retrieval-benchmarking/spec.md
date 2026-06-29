## ADDED Requirements

### Requirement: Reproducible A/B benchmark for a retrieval change

The system SHALL provide a reproducible two-arm benchmark, runnable through the existing
`archi evaluate` config-directory harness, that compares a baseline retrieval
configuration against a treatment configuration while holding every other variable
(embedding model, candidate-generation weights, system-under-test model, RAGAS judge,
and question bank) identical across the arms. The two arms SHALL ingest into separate
vectorstores so their indexes do not collide.

#### Scenario: A/B run emits a comparable result set

- **WHEN** the operator runs `archi evaluate` over the benchmark config directory containing the baseline and treatment arms
- **THEN** the run produces per-arm RAGAS aggregates plus a pairwise comparison and a leaderboard, and the shared-context record reports no drift in the held-fixed variables

#### Scenario: Arms differ only in the retrieval treatment

- **WHEN** the baseline and treatment configs are compared
- **THEN** they differ only in chunking strategy, retriever selection, arm name, and data path — all other fields are identical, so any measured difference is attributable to the retrieval treatment

#### Scenario: Each arm ingests an isolated corpus

- **WHEN** both arms are executed in one run
- **THEN** each arm builds its own vectorstore at a distinct data path (baseline character-split vs treatment hierarchical), and neither arm reuses the other's index

### Requirement: Grounded FASRC question banks in the harness schema

The system SHALL provide FASRC question banks consumable by the benchmark harness
(`queries_path`), where every record carries a `question` and, for RAGAS scoring, an
`answer` whose content is grounded in a real source rather than fabricated. At least one
bank SHALL tag records by question type so results can be sliced by difficulty
(simple retrieval vs multi-step reasoning vs out-of-scope refusal).

#### Scenario: Bank loads against the harness contract

- **WHEN** the harness loads a provided question bank for a RAGAS-mode run
- **THEN** every record exposes the required `question` and `answer` fields and the load does not raise a missing-field error

#### Scenario: Results can be sliced by question type

- **WHEN** a typed bank is used and results are analyzed
- **THEN** quality metrics can be reported separately for retrieval-only, reasoning, and should-refuse questions, so the analysis can show which question type the treatment affects

#### Scenario: Out-of-scope questions test refusal, not recall

- **WHEN** a should-refuse question (covering a system outside the FASRC corpus) is scored
- **THEN** the expected answer is a referral/acknowledgement of the gap, so a confident fabricated answer is counted as a failure

### Requirement: Measurement protocol covering quality, latency, and image size

The benchmark SHALL define and record three deltas between the arms: answer quality (the
RAGAS metrics), per-query latency, and the deployment image-size change introduced by the
treatment's dependencies. The latency measurement SHALL separate the treatment's one-time
reranker model-load cost from steady-state (warm) per-query latency.

#### Scenario: Quality delta is recorded per arm

- **WHEN** the benchmark completes
- **THEN** the four RAGAS metrics are recorded for each arm and the treatment-vs-baseline difference is reported

#### Scenario: Warm latency excludes the one-time model load

- **WHEN** per-query latency is reported for the treatment arm
- **THEN** the first query that pays the one-time reranker (FlashRank ONNX) model load is excluded or reported separately, so the reported warm latency reflects steady-state cost

#### Scenario: Image-size delta is recorded

- **WHEN** the treatment's dependencies (`llama-index-core` + `flashrank`) are added to the deployment image
- **THEN** the built-image size delta versus the baseline image is measured and recorded

### Requirement: Data-grounded recommendation

The benchmark effort SHALL conclude with a recommendation — whether to enable the
treatment by default, and recommended values for parent/child chunk sizes and
`bm25_weight` — that is justified by the recorded measurements and captured in a durable
decision record.

#### Scenario: Recommendation cites measured numbers

- **WHEN** the recommendation is written
- **THEN** each recommended setting references the benchmark numbers (quality, latency, and/or image-size) that justify it, rather than an unmeasured assumption

#### Scenario: Chunk-size recommendation reflects a sweep

- **WHEN** parent/child chunk sizes are recommended
- **THEN** the recommendation is informed by benchmark arms that actually varied those sizes (enabled by the configurable chunk sizes), or it explicitly states it covers the default sizes only
