## ADDED Requirements

### Requirement: Full-text index weights title and filename above body
The full-text index over `document_chunks` SHALL include the document title
(`display_name`) and filename with a higher weight than chunk body text, so that
title/filename matches rank above body-only matches in BM25/full-text scoring.

#### Scenario: Title match outranks incidental body match
- **WHEN** a query keyword appears in document A's title and only incidentally in
  document B's body
- **THEN** chunks of document A rank above chunks of document B in full-text results

#### Scenario: Ranking improves without re-embedding
- **WHEN** the weighted full-text index is applied to an existing corpus that has not
  been re-embedded
- **THEN** queries matching a title/filename keyword return the corresponding documents
  via the full-text path

### Requirement: Filename/title match boost in hybrid search
Hybrid search SHALL apply a configurable score boost to documents whose `display_name`
matches the query, reusing the existing trigram index on `documents.display_name`, so
documents with a matching filename are surfaced even when semantic and body BM25 scores
are low.

#### Scenario: Filename hit with weak body similarity
- **WHEN** a query keyword matches a document's filename but the body is not semantically
  similar to the query
- **THEN** that document appears in the hybrid results

#### Scenario: Boost is configurable
- **WHEN** the filename/title boost weight is set to zero in configuration
- **THEN** hybrid results are produced from BM25 and semantic scores only

### Requirement: Retrieval quality is benchmarked
The change SHALL be evaluated with the benchmarking harness, comparing retrieval quality
before and after, including queries whose keyword appears only in the title or filename.

#### Scenario: Before/after benchmark on title-only queries
- **WHEN** the benchmark is run on a query set that includes title-only and
  filename-only keyword queries
- **THEN** the harness reports retrieval metrics for the baseline and the new behavior,
  showing the recall change for those queries
