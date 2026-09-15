## MODIFIED Requirements

### Requirement: Grounded FASRC question banks in the harness schema

The system SHALL provide FASRC question banks consumable by the benchmark harness
(`queries_path`), where every record carries a `user_input` and, for RAGAS scoring, a
`reference` whose content is grounded in a real source rather than fabricated (the modern
RAGAS 0.3.5 dialect; legacy `question`/`answer` records are normalized on read). At least one
bank SHALL tag records by question type so results can be sliced by difficulty (simple
retrieval vs multi-step reasoning vs out-of-scope refusal).

A bank used to **evaluate a retrieval treatment** SHALL additionally satisfy the coverage
minimums below, because the unit of independent evidence for a retrieval treatment is the gold
**article**, not the question. Article-level metadata (such as the captured `category`) is
written identically onto every chunk of an article, so a treatment keyed on it moves all of that
article's questions together; two questions pointing at the same article are near-duplicates for
this purpose, not two observations.

**Source-group coverage.** A bank whose gold sources all belong to a single source group cannot
detect a treatment that improves that group by demoting another, so such a treatment would
measure as a clean win while silently regressing the rest of the corpus. The FASRC corpus
comprises the `docs.rc.fas.harvard.edu` Knowledge Base **and** non-KB sources
(`slurm.schedmd.com`, the namesake wiki page), so a bank used to evaluate a retrieval treatment
SHALL include gold sources from each.

**Benefit-side coverage.** To support any confidence-interval or significance-based claim about a
retrieval treatment, a bank SHALL carry **at least 30 distinct gold KB articles** spanning **at
least 6 distinct captured categories**, with **no single article contributing more than 10% of
the gold rows**. Below roughly 30 articles a nominal 95% cluster bootstrap interval delivers
materially less than 95% coverage, so any pass/fail rule built on it is not the rule it claims to
be. The present bank carries 18 gold rows over 7 articles, two of which hold 56% of the rows
between them.

The article minimum SHALL be **derived, not asserted**: the coverage and power figures that
underwrite it SHALL come from a deterministic, fixed-seed simulation over the bank's real
cluster-size vector, whose assumptions (seed, cluster sizes, assumed intracluster correlation, base
rate, replicate count) are published with its output so that a reader can re-run it. The minimum
MAY be changed **only** by a published, seeded re-derivation — never by an appeal to schedule,
convenience, or a judgment that a smaller bank is probably adequate.

**Harm-side coverage.** To support any claim that a treatment is harm-clean, a bank SHALL carry
**at least 12 independent at-risk units per harm channel**, because with zero harmful units
observed out of `n`, the 95% upper bound on the true harm rate is approximately `3 / n`. The unit
differs by channel and SHALL be counted, not assumed: for out-of-scope harm the unit is the
`should_refuse` anchor; for demotion of an unboostable source group the unit is the distinct
non-KB gold page; for in-corpus misrouting the unit is the gold **article**, not the gold row,
because a metadata-keyed boost moves every row of an article together.

**No authored label may feed a safety metric.** A bank SHALL NOT carry a per-question label from
which a harm or safety counter-metric is computed. Authoring the label that decides how much harm
an experiment is able to find makes the author the safety oracle; harm SHALL instead be measured
adversarially over the label space. Authored **gold sources** remain permitted and required — a
gold source is a verifiable fact about which page answers a question, not a prediction about a
hypothetical classifier.

Non-KB gold rows SHALL NOT be counted toward the benefit-side minimum: a non-KB document carries
no article-level KB metadata and can never receive a metadata-keyed boost, so it adds zero
benefit-side clusters. Source-group coverage and benefit-side power are distinct obligations and
SHALL NOT be conflated.

A bank that fails these minimums MAY still be used for regression monitoring, for structural
diagnostics, and to witness harm; it SHALL NOT be used to justify adopting or rejecting a
retrieval treatment.

#### Scenario: Bank loads against the harness contract

- **WHEN** the harness loads a provided question bank for a RAGAS-mode run
- **THEN** every record exposes the required `user_input` and `reference` fields (or legacy equivalents normalized on read) and the load does not raise a missing-field error

#### Scenario: Results can be sliced by question type

- **WHEN** a typed bank is used and results are analyzed
- **THEN** quality metrics can be reported separately for retrieval-only, reasoning, and should-refuse questions, so the analysis can show which question type the treatment affects

#### Scenario: Out-of-scope questions test refusal, not recall

- **WHEN** a should-refuse question (covering a system outside the FASRC corpus) is scored
- **THEN** the expected answer is a referral/acknowledgement of the gap, so a confident fabricated answer is counted as a failure

#### Scenario: Every corpus source group is represented by gold sources

- **WHEN** a bank is used to evaluate a retrieval treatment against the FASRC corpus
- **THEN** its gold sources include at least one `slurm.schedmd.com` page and at least one namesake-wiki page, so a treatment that lifts KB recall by demoting either non-KB group is visible rather than invisible

#### Scenario: Single-source-group bank is rejected for treatment evaluation

- **WHEN** a bank whose gold sources all belong to one source group is used to justify a retrieval treatment
- **THEN** the result is treated as unsound for that purpose, because the bank is structurally incapable of observing harm to the unrepresented groups

#### Scenario: Article-clustered bank is rejected for a powered claim

- **WHEN** a bank's gold rows resolve to fewer than 30 distinct KB articles, or a single article carries more than 10% of the gold rows
- **THEN** the bank is reported as unable to support a confidence-interval or significance claim about a retrieval treatment, and any such claim made on it is rejected

#### Scenario: The article minimum is derived, not asserted

- **WHEN** the 30-article minimum is cited, or a change to it is proposed
- **THEN** the cited coverage and power figures come from a published fixed-seed simulation whose seed, cluster-size vector, assumed intracluster correlation, base rate, and replicate count are stated, and the minimum moves only by re-running that derivation rather than by judgment

#### Scenario: Non-KB additions do not raise benefit-side power

- **WHEN** SchedMD and wiki gold rows are added to a bank
- **THEN** the coverage report credits them to the at-risk minimum only, and the distinct-KB-article count used for the benefit-side minimum is unchanged

#### Scenario: No authored label feeds a safety metric

- **WHEN** a bank is proposed that carries a per-question label used to compute a harm or safety counter-metric
- **THEN** the bank is rejected for that purpose, because the label's author would determine how much harm the experiment is able to find

#### Scenario: Coverage is reported, not assumed

- **WHEN** a bank is used for any treatment evaluation
- **THEN** the run emits the distinct gold-article count, the per-article row shares, the distinct-category count, and the per-channel at-risk unit counts, so a reader can check the minimums rather than trust them

### Requirement: Data-grounded recommendation

The benchmark effort SHALL conclude with a recommendation — whether to enable the treatment by
default, and recommended values for parent/child chunk sizes and `bm25_weight` — that is
justified by the recorded measurements, captured in a durable decision record, and stated at a
confidence the measurements can actually carry.

Every number cited in a recommendation SHALL be accompanied by the number of independent clusters
it rests on, its uncertainty interval, and the minimum effect the design could have detected. A
recommendation SHALL NOT be justified by a point estimate alone.

Where the cited measurements come from a bank that fails the coverage minimums of the "Grounded
FASRC question banks in the harness schema" requirement, no adopt-or-reject recommendation SHALL
be issued at all. In that case the recommendation is "expand the bank", and the underlying
question is recorded as still open rather than as settled in either direction.

#### Scenario: Recommendation cites measured numbers

- **WHEN** the recommendation is written
- **THEN** each recommended setting references the benchmark numbers (quality, latency, and/or image-size) that justify it, rather than an unmeasured assumption

#### Scenario: Chunk-size recommendation reflects a sweep

- **WHEN** parent/child chunk sizes are recommended
- **THEN** the recommendation is informed by benchmark arms that actually varied those sizes (enabled by the configurable chunk sizes), or it explicitly states it covers the default sizes only

#### Scenario: A point estimate is not sufficient

- **WHEN** a recommendation cites a quality delta
- **THEN** it also states the cluster count, the interval, and the minimum detectable effect, so the reader can see whether the delta is distinguishable from noise

#### Scenario: Underpowered evidence yields no recommendation

- **WHEN** the measurements come from a bank below the coverage minimums
- **THEN** the decision record states that no adopt-or-reject recommendation is supportable, names bank expansion as the prerequisite work, and does not record the question as settled
