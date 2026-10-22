## ADDED Requirements

### Requirement: QA preparation SHALL retry a gold extraction once after a shape violation
When `validate_gold_output` raises `ValueError` for an extractor result, preparation SHALL call `extract_gold` once more for that item and SHALL fail the item as `preparation_failed` only if the second result also violates the shape.
No retry SHALL happen for `OracleResolutionError`, for an exception raised by `extract_gold` itself, or for any other exception. A record whose extraction was retried SHALL carry `gold_extraction_attempts: 2` and the summed usage of both calls; a record without a retry SHALL serialise exactly as before.

#### Scenario: A shape violation followed by a valid result prepares the item
- **WHEN** the extractor's first result has a non-list `atoms` and its second result is valid
- **THEN** the item is `prepared` with `atom_source` `inferred`
- **AND** the record has `gold_extraction_attempts` 2
- **AND** the extractor was called exactly 2 times

#### Scenario: Two shape violations fail the item
- **WHEN** both extractor results violate the gold-atom shape
- **THEN** the item is `preparation_failed`
- **AND** the record has `gold_extraction_attempts` 2
- **AND** the extractor was called exactly 2 times

#### Scenario: An oracle failure is not retried
- **WHEN** the oracle resolver raises `OracleResolutionError` for a live item
- **THEN** the item is `preparation_failed`
- **AND** the extractor was not called again and the record has no `gold_extraction_attempts` key

#### Scenario: An extractor exception is not retried
- **WHEN** `extract_gold` raises an exception on its first call
- **THEN** the item is `preparation_failed` after exactly 1 call

#### Scenario: A retried record round-trips through the row reader
- **WHEN** a record with `gold_extraction_attempts` 2 is written to `preparation.jsonl` and read back
- **THEN** the reader returns an equal record and does not raise

#### Scenario: The retry's tokens are summed
- **WHEN** the first extractor call reports 100 input tokens and the retry reports 50 input tokens
- **THEN** the record's `usage.input_tokens` is 150 and `usage.calls` is 2

### Requirement: QA summary and report SHALL show scored items against total items
`summary.json` SHALL carry `items_total`, the prepare phase's `input_items`, and `scored_items`, the count of items with at least one `scored` attempt, and `report.md` SHALL print both in one header line.
Every existing summary key SHALL keep its name and meaning, and the CLI exit code SHALL not change.

#### Scenario: A partial run is visible in the summary
- **WHEN** a run has 3 input items and 1 item fails preparation
- **THEN** `summary.json` has `items_total` 3 and `scored_items` 2

#### Scenario: The report prints the coverage line
- **WHEN** the score phase writes `report.md` for that run
- **THEN** the header contains `Scored items: \`2\` / items: \`3\``

#### Scenario: An old summary still renders
- **WHEN** the report header is built from a summary that has no `items_total` or `scored_items`
- **THEN** the line prints `unavailable` for each missing value and no error is raised
