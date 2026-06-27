## MODIFIED Requirements

### Requirement: Re-ingest refreshes chunks for changed content
The system SHALL ensure that when a previously-indexed document's persisted content
changes under an unchanged hash, its stale chunks are not left in `document_chunks` —
the document's chunks SHALL be refreshed (or removed before re-embedding) so retrieval
never serves the old content alongside the new. Detection SHALL cover both a changed
persisted *filename* (e.g. HTML→Markdown, where the basename flips) AND a changed
*content signal* under an unchanged filename and unchanged hash (e.g. an in-place
re-scrape or local-file overwrite). The content-signal comparison SHALL be evaluated
only for hashes already present in the vectorstore, so a corpus with no changes still
reports "up to date" without per-document recomputation on the no-change fast path.

#### Scenario: Converted re-ingest does not leave stale chunks
- **WHEN** a document already indexed as raw HTML is re-ingested with conversion enabled (same hash, new `.md` content)
- **THEN** its old HTML-derived chunks are replaced by Markdown-derived chunks, not duplicated or left stale

#### Scenario: Same-filename content rewrite refreshes chunks
- **WHEN** a previously-indexed document is re-ingested with changed content but the same filename and same identity hash
- **THEN** its old chunks are removed and the new content is re-embedded, so the document's chunks are replaced — not duplicated and not left stale

#### Scenario: Unchanged corpus stays up to date
- **WHEN** every document is re-ingested with identical content, filename, and hash
- **THEN** no chunks are removed or re-embedded and the vectorstore is reported up to date

## ADDED Requirements

### Requirement: Persisted content signal for re-ingest staleness
The system SHALL persist, at ingest time, a per-document content signal sufficient to
detect a later content-only change under an unchanged filename and hash. The signal
SHALL reuse an existing `documents` column or chunk metadata (e.g. `size_bytes` /
`file_modified_at`, or a content hash) rather than introducing new schema where an
existing signal is sufficient. The signal SHALL be stored for documents that are
embedded into the vectorstore so it is available for comparison on the next re-ingest.

#### Scenario: Content signal recorded at ingest
- **WHEN** a document is ingested and embedded into the vectorstore
- **THEN** a content signal for that document is persisted alongside its chunks or in the `documents` catalog so a subsequent re-ingest can compare against it

#### Scenario: Missing content signal does not crash re-ingest
- **WHEN** a previously-indexed document has no recorded content signal (e.g. ingested before this change)
- **THEN** re-ingest falls back to the filename-based staleness check without error and leaves the no-change fast path intact
