## ADDED Requirements

### Requirement: Backend scores are brought to a common orientation before weighting

Every component score that `PostgresVectorStore.hybrid_search` blends SHALL be oriented so that a **higher** value means a **better** match, before any weight is applied. Where a backend reports its score in the opposite orientation, `hybrid_search` MUST convert it rather than consume it raw. Specifically, the `pg_textsearch` `<@>` operator returns negative BM25 scores (lower = better match, for ascending index scans), so its output MUST be negated before use. The final ordering SHALL be descending on the combined score.

#### Scenario: A keyword-matching chunk outranks a chunk with no keyword overlap

- **WHEN** `hybrid_search` scores a candidate set in which chunk A produces a strong BM25 match (a large-magnitude negative raw `<@>` value) and chunk B produces no keyword overlap (a raw `<@>` value of 0), and both chunks have comparable semantic scores
- **THEN** chunk A SHALL be ranked above chunk B in the returned results

#### Scenario: Keyword evidence never lowers a chunk's rank

- **WHEN** two candidates have identical semantic scores and candidate A has a stronger BM25 match than candidate B
- **THEN** candidate A's combined score SHALL be greater than or equal to candidate B's, and MUST NOT be lower

#### Scenario: A missing BM25 score is treated as absent keyword evidence

- **WHEN** the BM25 expression yields SQL `NULL` for a candidate
- **THEN** that candidate SHALL be scored as having the weakest keyword evidence in the set rather than being dropped or causing a `NULL` combined score

### Requirement: Component scores are normalized to a common scale before weighting

`hybrid_search` SHALL normalize each component score onto the range `0..1` across the scored candidate set before applying its configured weight, so that the weights govern the blend rather than the backends' incidental score ranges. The semantic component (`1 - cosine_distance`) is already bounded `0..1`; the BM25 component is unbounded in magnitude (measured `0..14.47` after negation on the dev corpus) and MUST be scaled, because an unscaled BM25 term of that magnitude overwhelms the entire achievable semantic range at any non-trivial weight.

#### Scenario: Ordering is invariant to BM25 score magnitude

- **WHEN** two candidate sets have BM25 scores whose relative order is identical but whose absolute magnitudes differ by a constant positive factor (for example, one corpus scoring `0..15` and another `0..150`), with semantic scores held equal
- **THEN** `hybrid_search` SHALL return the same ranking for both sets

#### Scenario: Weights govern the blend rather than the score ranges

- **WHEN** `hybrid_search` is called with `semantic_weight` and `bm25_weight` summing to `1.0`
- **THEN** every returned `combined_score` SHALL lie within `0..1`

#### Scenario: All candidates carrying the same BM25 score does not divide by zero

- **WHEN** every candidate in the scored set has an identical BM25 score — including the common case of a query whose terms match no chunk, where all raw scores are 0, making the set's maximum equal its minimum
- **THEN** `hybrid_search` SHALL complete without a division-by-zero error, and SHALL rank the candidates by their semantic component alone, since the keyword component carries no discriminating information

### Requirement: Configured weights express proportional contribution

`semantic_weight` and `bm25_weight` SHALL each express that component's proportional contribution to the combined score, applied to the normalized components. Setting a component's weight to `0` SHALL remove that component's influence on the ordering entirely.

#### Scenario: Zero BM25 weight yields semantic-only ordering

- **WHEN** `hybrid_search` is called with `bm25_weight=0.0` and `semantic_weight=1.0`
- **THEN** the returned ordering SHALL match the ordering by semantic score alone

#### Scenario: Zero semantic weight yields keyword-only ordering

- **WHEN** `hybrid_search` is called with `semantic_weight=0.0` and `bm25_weight=1.0`
- **THEN** the returned ordering SHALL match the ordering by BM25 match strength alone, strongest match first

#### Scenario: Returned scores are relative to the scored set

- **WHEN** a caller receives `(Document, combined_score)` tuples from `hybrid_search`
- **THEN** the documented contract SHALL state that `combined_score` is normalized within that query's candidate set and is therefore **not** comparable across different queries, so it MUST NOT be used as an absolute relevance threshold

### Requirement: One source of truth for the semantic and BM25 weight defaults

The default `semantic_weight` / `bm25_weight` pair SHALL be defined in exactly one place in the codebase, and every construction path SHALL derive its default from it. No two code paths may declare different default pairs.

#### Scenario: All construction paths agree on the defaults

- **WHEN** the default weights are read from the `hybrid_search` signature, from the `HybridRetriever` dataclass, and from the `build_vector_retriever` factory's config fallbacks
- **THEN** all three SHALL yield the same `(semantic_weight, bm25_weight)` pair

#### Scenario: Rendered config and database defaults match the code default

- **WHEN** `base-config.yaml` is rendered with no `data_manager.retrievers.hybrid_retriever` overrides, and the `init.sql` dynamic-config column defaults for `semantic_weight` / `bm25_weight` are read
- **THEN** both SHALL equal the single code-level default pair

### Requirement: Tests encode the backends' real score conventions

Unit tests that stand in for the database SHALL mock component scores using the same sign convention the real backend produces, so that reintroducing an orientation error fails the suite. A test fixture MUST NOT assert a score orientation the backend cannot produce.

#### Scenario: Mocked BM25 rows use the backend's negative convention

- **WHEN** a unit test supplies fake `bm25_score` values to exercise `hybrid_search`
- **THEN** those values SHALL follow the `pg_textsearch` convention the raw operator emits — negative or zero, lower meaning a better match — rather than positive values

#### Scenario: Reintroducing the sign inversion fails the suite

- **WHEN** the negation of the BM25 term is removed from the scoring expression, restoring the pre-change behavior
- **THEN** at least one unit test SHALL fail
