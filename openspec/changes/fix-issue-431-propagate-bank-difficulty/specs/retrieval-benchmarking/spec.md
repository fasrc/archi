## ADDED Requirements

### Requirement: A bank row's difficulty reaches the per-question result

The benchmark harness SHALL copy a bank row's `difficulty` value verbatim onto that question's `single_question_results` entry, SHALL write no `difficulty` key at all when the row carries none, and SHALL do so whether the question was scored or failed in isolation.

The analysis the project asks for depends on it. Procedure D
(`docs/docs/interpreting_benchmark_results.md:690`) instructs a reader to "Report the bank
sliced by `difficulty`, not as one number", and `compare_runs.py` implements that slice —
`SLICE_FIELDS = ("anchor_type", "difficulty")` at
`scripts/benchmarking/compare_runs.py:85`. The tool slices on a field only when both arms
carry it, and no artifact the harness writes carries this one, so the instruction and the
tool have never met.

The field survives every stage but the last. `normalize_record`
(`src/utils/benchmark_schema.py:109`) preserves extension fields verbatim, and
`single_question_results` (`src/bin/service_benchmark.py:408`) stores the per-question
dicts with no key allowlist. Only the result-building step drops it, by copying
`anchor_type` alone.

Absence is written as absence, not as a value. `anchor_type` is copied unconditionally with
a `""` fallback, because the anchor path sets it on every anchor row and `""` there means
"not an anchor". `difficulty` has no such producer, and an always-present `""` would read
downstream as a field present in both arms with one empty group — a slice reported over a
single meaningless bucket, which is a worse answer than a skipped slice because it looks
like a result. It would also change every artifact produced from a bank without the field,
including the FASRC bank.

The harness SHALL NOT validate the value. Constraining it to `easy` / `medium` / `hard`
belongs to the bank-maintenance tooling, which owns bank vocabulary; the scoring path does
not police `anchor_type` values either.

#### Scenario: A row carrying difficulty propagates it

- **WHEN** a bank row carrying `difficulty: "hard"` is answered and scored
- **THEN** that question's `single_question_results` entry carries `difficulty` with the value `"hard"`

#### Scenario: A row without difficulty produces no key

- **WHEN** a bank row carrying no `difficulty` field is answered and scored
- **THEN** that question's `single_question_results` entry has no `difficulty` key
- **AND** the entry is not given a `null` or empty-string `difficulty` instead

#### Scenario: The harness writes the key the comparison tool slices on

- **WHEN** the key the harness writes for a row's difficulty is compared against the slice fields the paired-comparison tool reads
- **THEN** the key is one of those slice fields

The producer and the consumer sit in two files with no shared import; the only thing
joining them is the spelling of one string. A rename on either side would leave the slice
silently skipped again, which is the state this requirement exists to end.

#### Scenario: A question that failed in isolation keeps the bank's slice fields

- **WHEN** a bank row carrying `difficulty: "hard"` raises during answering or scoring and is recorded as a failure entry
- **THEN** that failure entry carries `difficulty` with the value `"hard"`
- **AND** it carries the row's `anchor_type` on the same terms
- **AND** a paired comparison in which that question failed in one arm only counts it in no field's `excluded_mismatched`

The isolation handler builds its entry from scratch, so it did not reach the copy on the
success path. That is not a cosmetic gap. `Arm.has_metric`
(`scripts/benchmarking/compare_runs.py:209`) is true when *any* row carries the field, so a
single failed question in the treatment arm made `slice_block` read the baseline's `"hard"`
against an absent value and count it in `excluded_mismatched` — a number whose documented
meaning is that the bank re-labelled that question between the two runs. A harness failure
reported as bank drift sends the reader to diff a bank that never changed.
