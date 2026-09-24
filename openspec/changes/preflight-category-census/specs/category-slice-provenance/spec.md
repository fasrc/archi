## ADDED Requirements

### Requirement: One attribution module for the census and the slice
The system SHALL compute row ownership, the cross-category and uncategorized lines, unresolved URLs, category coverage, per-article row share and per-metric power in one module, `scripts/benchmarking/category_attribution.py`, and both the preflight census and the compare-runs slice SHALL call it.

#### Scenario: Same row, same answer
- **WHEN** the census and the slice attribute the same bank against the same URL → category map
- **THEN** every row gets the same owner and the same side-line membership in both

#### Scenario: Concentration counts every citation
- **WHEN** an article is the second source on 20 % of rows and never a first source
- **THEN** its row share is 20 %

### Requirement: KB category coverage census
The system SHALL count non-deleted documents whose canonical URL contains `/kb/` and report the share with a non-empty `category`, and SHALL fail the preflight when that share is below 90 %.

#### Scenario: Deleted rows ignored
- **WHEN** a re-ingest left an `is_deleted = true` copy of a KB page with no category
- **THEN** the copy is not counted

#### Scenario: Below threshold
- **WHEN** 85 % of KB documents carry a category
- **THEN** the census exits 2 and names the share

### Requirement: Vocabulary drift census scoped to the routing prompt
The system SHALL compare the distinct `category` values over the same KB documents with the bullet list under `## Category routing` in the given routing prompt, and SHALL fail naming each label present on only one side.

#### Scenario: Indico categories out of scope
- **WHEN** Indico documents (not under `/kb/`) carry their own event categories
- **THEN** those values do not enter the comparison

#### Scenario: Label missing from the corpus
- **WHEN** the prompt lists `IQSS` and no KB document carries it
- **THEN** the census fails and names `IQSS` as prompt-only

### Requirement: Bank coverage and concentration census
The system SHALL join the bank's gold URLs to the live map with the shared URL rule, SHALL report per category the owned gold rows and the coverage, SHALL fail when fewer than 6 categories have coverage or any article's row share — rows citing it in any source position divided by the gold rows, the bank rows that declare at least one source — exceeds 10 %, and SHALL report underpowered categories and the cross-category, uncategorized and unresolved lines without dropping any row.

#### Scenario: Six categories required
- **WHEN** the bank's sources cover five categories
- **THEN** the census exits 2 naming the count

#### Scenario: One article dominates
- **WHEN** one article is cited by 11 % of gold rows
- **THEN** the census exits 2 naming the article and its share

#### Scenario: Output labelled with the corpus it read
- **WHEN** the census completes
- **THEN** the Markdown and JSON outputs carry the corpus fingerprint computed with the harness's `CORPUS_STATE_QUERY`

### Requirement: r0b exemplars are disjoint from the bank and anchors
The system SHALL extract every `Question:` block and every URL cited in the `Answer:` blocks under `## Worked examples` of the given exemplar prompt, and SHALL fail when an exemplar question equals a bank or anchor question after normalization or has a word-set Jaccard similarity of 0.5 or more with one, or when a cited URL equals a bank or anchor source after canonicalization.

#### Scenario: Clean exemplars
- **WHEN** the r0b prompt at archi-config PR #22 is checked against the 105-row bank and the 5 anchors
- **THEN** the check passes and records the exemplar count and the sha256 of the prompt it read

#### Scenario: Paraphrased bank question
- **WHEN** an exemplar question shares at least half its word set with a bank question
- **THEN** the check fails and names both questions

#### Scenario: Shared citation
- **WHEN** an exemplar answer cites a URL that a bank row declares as a source, with or without a trailing slash
- **THEN** the check fails and names the URL

### Requirement: The metadata substring fallback is pinned by a test
The system SHALL have a unit test asserting that `_build_extra_text` emits `key:value` followed by `value` for each non-null payload entry, and that `search_metadata` with a key outside `_METADATA_COLUMN_MAP` builds an `extra_text ILIKE %s` clause with parameter `%key:value%`.

#### Scenario: Category key uses the fallback
- **WHEN** `search_metadata` is called with `{"category": "Storage"}`
- **THEN** the generated SQL contains `extra_text ILIKE %s` and the parameters contain `%category:Storage%`
