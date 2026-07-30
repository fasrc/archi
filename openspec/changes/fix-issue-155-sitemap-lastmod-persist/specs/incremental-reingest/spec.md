## ADDED Requirements

### Requirement: Sitemap parsing captures each entry's optional lastmod

The system SHALL, when parsing a sitemap document, capture the optional `<lastmod>`
text of each direct `<url>` (in a `<urlset>`) or `<sitemap>` (in a `<sitemapindex>`)
entry alongside its `<loc>`, exposing them as a `(loc, lastmod)` pair where `lastmod` is
the trimmed `<lastmod>` text or `None`. A missing `<lastmod>`, an empty `<lastmod>`, or a
malformed document SHALL yield `None` for that entry (or no entries) and SHALL NOT raise
beyond the parser's existing `SitemapParseError` contract — capture stays fail-open.

#### Scenario: Entry with a lastmod yields its value
- **WHEN** a `<urlset>` `<url>` contains `<loc>https://example.org/a</loc>` and `<lastmod>2026-04-21T19:19:35+00:00</lastmod>`
- **THEN** parsing yields the pair `("https://example.org/a", "2026-04-21T19:19:35+00:00")`

#### Scenario: Entry without a lastmod yields None
- **WHEN** a `<url>` contains a `<loc>` but no `<lastmod>` (or an empty `<lastmod></lastmod>`)
- **THEN** parsing yields that loc paired with `None`

#### Scenario: Malformed document never raises for lastmod capture
- **WHEN** a document is malformed such that a `<lastmod>` cannot be read
- **THEN** the capture path yields `None` for the affected entries and raises no exception beyond the existing parse contract

### Requirement: Existing sitemap parse callers keep their current behavior

The system SHALL preserve the current output shape and behavior of every existing caller
of the sitemap parse helpers — `src/cli/tools/sources_builder.py` and
`src/utils/goldenset_maintenance.py` — so this change adds the lastmod signal without
altering the set of URLs those callers already produce.

#### Scenario: sources_builder output unchanged
- **WHEN** `sources_builder` expands the same sitemap before and after this change
- **THEN** it emits the identical set of page URLs, unaffected by lastmod capture

#### Scenario: goldenset expansion output unchanged
- **WHEN** `goldenset_maintenance` calls the expansion helpers on the same sitemap
- **THEN** the emitted page-URL set is identical to today's

### Requirement: Sitemap expansion carries lastmod per emitted URL

The system SHALL, in `expand_sitemap_source` and `expand_sitemaps`, associate the
captured `lastmod` (or `None`) with each emitted, normalized page URL, preserving the
existing order-preserving dedupe, trust filtering, and per-source floor/cap semantics.
When two entries normalize to the same page URL, the first occurrence's URL and its
`lastmod` SHALL win, matching the existing first-seen dedupe.

#### Scenario: Emitted page carries its lastmod
- **WHEN** a trusted sitemap page with a `<lastmod>` survives normalization and dedupe
- **THEN** expansion emits that normalized URL paired with its captured `lastmod`

#### Scenario: Page without lastmod carries None
- **WHEN** an emitted page had no `<lastmod>`
- **THEN** expansion pairs that normalized URL with `None`

#### Scenario: Dedupe keeps the first occurrence's lastmod
- **WHEN** two entries normalize to the same URL with different lastmod values
- **THEN** the first-seen URL is emitted once with its own lastmod, and the duplicate is dropped

### Requirement: The documents catalog persists a last_modified value

The system SHALL provide a nullable `last_modified` column on the Postgres `documents`
catalog — present in the base schema (`src/cli/templates/init.sql`) for fresh installs
and added to existing databases by a forward-only, idempotent migration under
`src/cli/templates/migrations/` (safe to run more than once). `upsert_resource` SHALL
store a `last_modified` value carried in a resource's metadata into that column on both
insert and conflict-update, and SHALL store `NULL` when no such value is present. Rows
that predate the column SHALL read as `NULL` with no backfill.

#### Scenario: upsert stores a provided last_modified
- **WHEN** a resource is upserted with `last_modified` in its metadata
- **THEN** the value is written to the `documents.last_modified` column (and updated on a subsequent conflict-upsert)

#### Scenario: upsert without last_modified stores NULL
- **WHEN** a resource is upserted with no `last_modified` in its metadata
- **THEN** the `documents.last_modified` column is `NULL` and no error occurs

#### Scenario: Migration is idempotent
- **WHEN** the migration runs against a database that already has the column
- **THEN** it completes without error and the column is unchanged

### Requirement: Sitemap-derived pages carry their lastmod to persistence

The system SHALL thread a sitemap-derived page's captured `lastmod` from ingest-time
expansion into that page's persisted resource metadata, so a real ingest populates
`documents.last_modified` for pages the sitemap dated, and leaves it `NULL` for pages
without a sitemap `<lastmod>` and for non-sitemap sources.

#### Scenario: Sitemap page persists its lastmod
- **WHEN** a page expanded from a sitemap with a `<lastmod>` is scraped and persisted
- **THEN** its `documents` row's `last_modified` holds that captured value

#### Scenario: Non-sitemap page has no lastmod
- **WHEN** a hand-listed (non-sitemap) URL is scraped and persisted
- **THEN** its `documents.last_modified` is `NULL`

### Requirement: Fetch behavior is unchanged

The system SHALL NOT change which pages are fetched, skipped, or the order they are
collected as a result of capturing and persisting `last_modified`. A fresh ingest SHALL
still fetch every page it fetches today; no fetch-skip logic is introduced by this change.

#### Scenario: Fresh ingest still fetches every page
- **WHEN** an ingest runs over a sitemap after this change
- **THEN** the same set of pages is fetched as before, with `last_modified` recorded as an additional stored attribute only
