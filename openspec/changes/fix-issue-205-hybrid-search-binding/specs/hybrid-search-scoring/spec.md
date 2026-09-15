## ADDED Requirements

### Requirement: Supplied parameters correspond positionally to the SQL placeholders they fill

Every parameter `hybrid_search` passes to `cursor.execute` SHALL fill the placeholder it is intended for. The user's query text MUST reach the BM25 query expression, and the collection name MUST reach the collection predicate, regardless of where those fragments appear in the assembled statement. The parameter sequence SHALL be derived in the same construction as the SQL fragments, so that reordering a fragment cannot silently break the correspondence.

#### Scenario: The query text reaches the BM25 expression

- **WHEN** `hybrid_search` builds its statement for query text `Q` against collection `C`
- **THEN** the parameter bound to the `to_bm25query()` placeholder SHALL be `Q`, and the parameter bound to the collection-equality placeholder SHALL be `C`

#### Scenario: Metadata filters do not shift the correspondence

- **WHEN** `hybrid_search` is called with a metadata `filter` adding further `WHERE` placeholders
- **THEN** the query text and collection name SHALL still each reach their intended placeholder, and every filter value SHALL reach its own predicate

#### Scenario: Moving a scored expression cannot silently misalign the parameters

- **WHEN** the BM25 or semantic score expression is relocated within the statement, changing its placeholder's ordinal position
- **THEN** a test SHALL fail unless the parameter sequence is updated to match

### Requirement: A degraded retrieval path is observable

When `hybrid_search` cannot return hybrid results and falls back to semantic-only retrieval, it SHALL emit a warning-level record carrying **structured fields** that identify the event. Silent degradation is not acceptable: a hybrid search that is not running MUST NOT be indistinguishable from one that is.

Those fields SHALL be present in the **emitted output**, not merely attached to the in-process log record. Attaching a field to a `LogRecord` is not the same as emitting it: the configured formatter decides what reaches the collected stream, and a formatter that renders only the message discards every field supplied out-of-band. An implementation whose fields are visible to a test that inspects records, but absent from what the deployment collects, does not satisfy this requirement — it reproduces the very defect this capability exists to prevent, a check that passes while production stays blind.

The record SHALL NOT include the raw query text. User queries may contain personal or confidential content, and warning-level records typically reach centralized logging with broader access and longer retention than conversation storage — so emitting query text here would move user content into a less-governed store as a side effect of a diagnostic. Diagnosing this fallback does not require the query's content: the fields below identify it, and an operator who needs the text can reach it through the conversation store under its existing controls.

#### Scenario: The empty-result fallback warns with structured, non-sensitive fields

- **WHEN** the hybrid statement returns zero rows and `hybrid_search` falls back to `similarity_search_with_score`
- **THEN** a warning-level record SHALL be emitted before returning, carrying a fallback-reason field, the collection name, the requested `k`, and a request or trace identifier for correlation — and **not** the query text

#### Scenario: Query content does not reach the log record

- **WHEN** the fallback warning is emitted for a query containing a distinctive string
- **THEN** that string SHALL NOT appear anywhere in the emitted record

#### Scenario: Observability tests assert on fields, not prose

- **WHEN** a test verifies the fallback is observable
- **THEN** it SHALL assert on the structured field values rather than exact message wording, so that rephrasing the message does not break the test and so the test cannot be satisfied by prose that omits the fields

#### Scenario: The fields survive the configured formatter

- **WHEN** the fallback record is rendered through the formatter the application actually configures, rather than read as record attributes
- **THEN** every required field's key and value SHALL appear in the formatted output. A test that inspects only in-process record attributes SHALL NOT satisfy this scenario, because it passes identically whether or not the deployment emits the fields.

#### Scenario: A successful hybrid query does not warn

- **WHEN** the hybrid statement returns at least one row
- **THEN** no fallback warning SHALL be emitted

### Requirement: Component scores are oriented higher-is-better before weighting

Every component score blended into the combined score SHALL be oriented so a higher value means a better match, before any weight is applied. Where a backend reports the opposite orientation, `hybrid_search` MUST convert it rather than consume it raw — the `pg_textsearch` `<@>` operator returns negative BM25 scores (lower = better, for ascending index scans), so its output MUST be negated. The final ordering SHALL be descending on the combined score.

#### Scenario: A keyword-matching chunk outranks a chunk with no keyword overlap

- **WHEN** `hybrid_search` scores a set in which chunk A yields a strong BM25 match (a large-magnitude negative raw `<@>` value) and chunk B yields no keyword overlap (raw value 0), with comparable semantic scores
- **THEN** chunk A SHALL rank above chunk B in the returned results

#### Scenario: Keyword evidence never lowers a chunk's rank

- **WHEN** two candidates have equal semantic scores and candidate A has a stronger BM25 match than candidate B
- **THEN** candidate A's combined score SHALL be greater than or equal to candidate B's, and MUST NOT be lower

#### Scenario: A missing BM25 score is treated as absent keyword evidence

- **WHEN** the BM25 expression yields SQL `NULL` for a candidate
- **THEN** that candidate SHALL be scored as having the weakest keyword evidence in the set, rather than being dropped or producing a `NULL` combined score

### Requirement: Component scores are normalized to a common scale before weighting

`hybrid_search` SHALL normalize each component onto `0..1` across the scored candidate set before applying its weight, so the weights govern the blend rather than the backends' incidental ranges. Normalization SHALL occur within the statement, before any row limit is applied, so that candidate selection itself is corrected. Both components SHALL be normalized: the BM25 component is unbounded in magnitude, and the semantic component is not reliably `0..1` either, because `1 - distance` is applied for every configured `distance_metric` including `l2` (unbounded) and `inner_product` (`0..2`).

#### Scenario: Ordering is invariant to BM25 magnitude

- **WHEN** two candidate sets have BM25 scores in identical relative order but magnitudes differing by a constant positive factor, with semantic scores held equal
- **THEN** `hybrid_search` SHALL return the same ranking for both

#### Scenario: Normalization precedes candidate selection

- **WHEN** more candidates match than the requested `k`
- **THEN** the `k` returned candidates SHALL be those ranked highest by the normalized combined score, not a subset pre-selected by an unnormalized ordering

#### Scenario: Weights govern the blend rather than the score ranges

- **WHEN** `hybrid_search` is called with `semantic_weight` and `bm25_weight` summing to `1.0`
- **THEN** every returned `combined_score` SHALL lie within `0..1`

#### Scenario: A uniform BM25 score across candidates does not divide by zero

- **WHEN** every candidate in the scored set carries an identical BM25 score — including the common case of a query whose terms match no chunk, where all raw scores are 0 and the set's maximum equals its minimum
- **THEN** `hybrid_search` SHALL complete without a division-by-zero error and SHALL rank candidates by the semantic component alone, since the keyword component carries no discriminating information

### Requirement: Configured weights express proportional contribution

`semantic_weight` and `bm25_weight` SHALL each express that component's proportional contribution, applied to the normalized components. Setting a weight to `0` SHALL remove that component's influence on the ordering entirely.

#### Scenario: Zero BM25 weight yields semantic-only ordering

- **WHEN** `hybrid_search` is called with `bm25_weight=0.0` and `semantic_weight=1.0`
- **THEN** the ordering SHALL match ordering by the semantic component alone

#### Scenario: Zero semantic weight yields keyword-only ordering

- **WHEN** `hybrid_search` is called with `semantic_weight=0.0` and `bm25_weight=1.0`
- **THEN** the ordering SHALL match ordering by BM25 match strength alone, strongest first

#### Scenario: Returned scores are relative to the scored set

- **WHEN** a caller receives `(Document, combined_score)` tuples from `hybrid_search`
- **THEN** the documented contract SHALL state that `combined_score` is normalized within that query's candidate set and is therefore not comparable across queries, so it MUST NOT be used as an absolute relevance threshold

### Requirement: Tests exercise the executed statement, not a stand-in for it

Because `hybrid_search` delegates scoring and ordering to PostgreSQL and returns the database's computed score unchanged, correctness of the ranking SHALL be verified either against a real database or by asserting on the generated statement and its parameter sequence. Tests that supply fabricated result rows SHALL NOT be relied upon to guard scoring behavior, and any fabricated component score MUST follow the sign convention the real backend emits.

#### Scenario: Mocked BM25 rows use the backend's negative convention

- **WHEN** a unit test supplies fabricated `bm25_score` values
- **THEN** those values SHALL be negative or zero, as the raw `<@>` operator emits, rather than positive

#### Scenario: Every scoring invariant has its own database-executed test

- **WHEN** the suite covers the scoring invariants — parameter correspondence, BM25 orientation, normalization applied before candidate selection, the zero-range degenerate case, and `NULL` placement
- **THEN** **each** invariant SHALL have its own named test that executes the generated statement against a real PostgreSQL instance with `pg_textsearch` installed, and each SHALL individually report as executed and passed. An aggregate count of executed database tests SHALL NOT satisfy this requirement, because a single happy-path database test would otherwise discharge coverage for every invariant while the consequential cases stayed mocked or skipped.

#### Scenario: A wholly-skipped database suite is a failure, not a pass

- **WHEN** the database-executed portion of the suite runs in an environment where `pg_textsearch` is unavailable, or where any named invariant test above is skipped for any other reason
- **THEN** the run SHALL fail rather than report success — a zero or incomplete count of executed invariant tests MUST be treated as a failing condition, because reporting the skip while passing recreates exactly the coverage gap that allowed an unexecuted-SQL defect to ship

#### Scenario: Each invariant test fails when its own invariant is broken

- **WHEN** any single correction is individually reverted — the parameter ordering, the BM25 negation, the normalization, its placement before the row limit, or the zero-range guard
- **THEN** the named database-executed test for *that specific* invariant SHALL fail. Verifying only that the suite fails when the extension is absent tests the counter rather than the coverage, and does not satisfy this scenario.

#### Scenario: Reintroducing the sign inversion fails the suite

- **WHEN** the negation of the BM25 term is removed from the scoring expression
- **THEN** at least one test SHALL fail

#### Scenario: Reintroducing the binding defect fails the suite

- **WHEN** the parameter sequence is reordered so the collection name reaches the BM25 expression
- **THEN** at least one test SHALL fail
