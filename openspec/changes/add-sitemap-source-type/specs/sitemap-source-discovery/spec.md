## ADDED Requirements

### Requirement: Sitemap source prefix recognition

The ingestion source-list parser SHALL recognize lines beginning with the `sitemap-` prefix and route the remainder of the line (the sitemap URL) to sitemap expansion. Prefix routing MUST remain pure list-parsing: recognizing a `sitemap-` line SHALL NOT itself perform any network I/O. The explicit `sitemap-` prefix MUST take precedence over the elog/indico URL auto-detection heuristics.

#### Scenario: Sitemap line peeled into the sitemap bucket

- **WHEN** an input list contains the line `sitemap-https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml`
- **THEN** the URL `https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml` (prefix stripped) is routed to sitemap expansion
- **AND** it is not routed to the plain-link, git, sso, elog, or indico buckets
- **AND** no network request is made during list parsing

#### Scenario: Explicit prefix beats auto-detection

- **WHEN** an input list contains a `sitemap-` line whose URL path contains `/elog/`
- **THEN** the URL is routed to sitemap expansion, not to the elog bucket

### Requirement: Non-sitemap lines are unaffected

Source-list lines without the `sitemap-` prefix MUST route exactly as they do today: `git-`, `sso-`, `elog-`, and `indico-` prefixed lines to their respective collectors, auto-detected elog/indico URLs per the existing heuristics, and all remaining lines to the plain web-link path. A deployment whose input lists contain no `sitemap-` lines SHALL observe no behavior change.

#### Scenario: Mixed list routes as before

- **WHEN** an input list contains a plain URL, a `git-` line, and a `sitemap-` line
- **THEN** the plain URL is collected as a web link and the `git-` line is collected by the git scraper, both exactly as before
- **AND** only the `sitemap-` line is routed to sitemap expansion

#### Scenario: No sitemap lines, no change

- **WHEN** no input list contains a `sitemap-` line
- **THEN** no sitemap expansion is attempted and the ingestion run is byte-for-byte identical to current behavior

### Requirement: Urlset expansion to page URLs

For each sitemap URL, the expander SHALL fetch the document and, when its root element is `<urlset>`, emit the text of every `<loc>` child as a page URL. `<loc>` extraction MUST be namespace-agnostic (the sitemap namespace `http://www.sitemaps.org/schemas/sitemap/0.9` may or may not be declared). `<lastmod>` values SHALL be ignored. An otherwise-valid `<urlset>` with zero `<loc>` entries SHALL contribute no URLs and SHALL NOT be treated as an error.

#### Scenario: Flat urlset expanded

- **WHEN** the fetched sitemap is a `<urlset>` containing 282 `<loc>` entries, each with a `<lastmod>`
- **THEN** exactly the 282 page URLs are emitted (after normalization and deduplication)
- **AND** the `<lastmod>` values have no effect on the output

#### Scenario: Namespace not declared

- **WHEN** the fetched `<urlset>` does not declare the sitemap XML namespace
- **THEN** its `<loc>` entries are still extracted and emitted

### Requirement: Sitemapindex recursed one level

When the fetched document's root element is `<sitemapindex>`, the expander SHALL fetch each child sitemap referenced by a `<loc>` exactly once and emit the page URLs of children whose root is `<urlset>`. A child that is itself a `<sitemapindex>` SHALL NOT be followed further and SHALL contribute no URLs.

#### Scenario: Index of urlsets expanded

- **WHEN** the fetched sitemap is a `<sitemapindex>` referencing two child sitemaps, each a `<urlset>`
- **THEN** each child sitemap is fetched exactly once
- **AND** the emitted page URLs are the union of both children's `<loc>` entries

#### Scenario: Nested index contributes nothing

- **WHEN** a `<sitemapindex>` child is itself a `<sitemapindex>`
- **THEN** that child is not followed further
- **AND** it contributes no page URLs
- **AND** sibling `<urlset>` children still contribute their page URLs

### Requirement: Fetch or parse failure fails open

A fetch failure (connection error, timeout, non-200 status) or parse failure (malformed XML, DTD/entity declaration, unrecognized root element) on any sitemap document MUST NOT abort the ingestion run. The expander SHALL log a warning identifying the failing sitemap URL, contribute zero URLs from that document, and continue with any remaining sitemap documents. All other configured sources SHALL be collected normally.

#### Scenario: Unreachable sitemap skipped

- **WHEN** the sitemap fetch raises a connection error or returns a non-200 status
- **THEN** a warning naming the sitemap URL is logged
- **AND** that sitemap contributes an empty URL list
- **AND** the ingestion run continues and collects all other configured sources

#### Scenario: Malformed XML skipped

- **WHEN** the fetched body is not well-formed XML, contains a `<!DOCTYPE` or `<!ENTITY` declaration, or has a root element other than `<urlset>`/`<sitemapindex>`
- **THEN** a warning is logged and that document contributes no URLs
- **AND** no exception propagates to the ingestion run

#### Scenario: Partial failure yields partial results

- **WHEN** two `sitemap-` lines are configured and exactly one fetch fails
- **THEN** the page URLs from the successful sitemap are still emitted and scraped

### Requirement: Trailing-slash normalization of expanded URLs

Every emitted page URL SHALL be normalized before joining the web-link collection: the fragment dropped, the scheme and host lowercased, the query preserved, and a single trailing path slash collapsed (the root path `/` preserved) — matching the hand-list URL form. Emitted URLs MUST be deduplicated order-preservingly, both within the expansion and against web-link URLs already collected from the input lists.

#### Scenario: Sitemap slash form normalized to hand-list form

- **WHEN** a sitemap `<loc>` reads `https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp/`
- **THEN** the emitted URL is `https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp` (no trailing slash)

#### Scenario: Slash-variant duplicates collapse

- **WHEN** the expansion would emit both `.../scp` and `.../scp/`, or an expanded URL equals a hand-listed web link already collected
- **THEN** the URL is scraped only once

### Requirement: Expanded URLs flow through the standard web-scraping path

Page URLs produced by sitemap expansion SHALL be appended to the web-link collection before standard link collection runs, and thereafter treated exactly as if each URL had been listed by hand: crawled at the configured `base_source_depth`, fetched, persisted, and processed by the same downstream pipeline with no sitemap-specific handling.

#### Scenario: Expanded URL scraped like a hand-listed URL

- **WHEN** sitemap expansion emits a page URL
- **THEN** the URL is collected by the standard web-link collector in the same run
- **AND** it is crawled at the configured base source depth
- **AND** the persisted resource is indistinguishable from one produced by a hand-listed line for the same URL
